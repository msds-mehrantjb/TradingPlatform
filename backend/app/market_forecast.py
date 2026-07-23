from __future__ import annotations

import importlib.util
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from backend.app.algorithms.voting_ensemble.strategies.registry import VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES
from backend.app.execution.cost_model import EXECUTION_COST_MODEL_SERVICE


FORECAST_HORIZON_MINUTES = 5
MARKET_FORECAST_POSITION_HORIZONS_MINUTES = (5, 10, 15)
MULTI_HORIZON_POSITION_FORECAST_POLICY = "advisory_only_until_live_paper_validation"
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
DEFAULT_DECISION_TO_SUBMISSION_LATENCY_SECONDS = 0.35
MIN_EXECUTION_FILL_PROBABILITY = 0.45
MAX_LIMIT_ORDER_NON_FILL_PROBABILITY = 0.55
MAX_STOP_LIMIT_MISS_PROBABILITY = 0.25
MAX_OPPORTUNITY_DECAY = 0.35
HIGH_VOLATILITY_THRESHOLD_ADJUSTMENT = 0.08
SIDEWAYS_THRESHOLD_ADJUSTMENT = 0.05
HIGH_VOLATILITY_POSITION_SIZE_MULTIPLIER = 0.5
SIDEWAYS_POSITION_SIZE_MULTIPLIER = 0.65
MODEL_VERSION = "market_forecast_v11"
MODEL_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "data" / "market_forecast"
FORECAST_ARTIFACT_ROOT = MODEL_ARTIFACT_DIR / "artifacts"
FORECAST_CANDIDATE_ARTIFACT_DIR = FORECAST_ARTIFACT_ROOT / "candidates"
FORECAST_ACTIVE_ARTIFACT_DIR = FORECAST_ARTIFACT_ROOT / "active"
FORECAST_ACTIVE_HISTORY_DIR = FORECAST_ARTIFACT_ROOT / "active_history"
FORECAST_REJECTED_ARTIFACT_DIR = FORECAST_ARTIFACT_ROOT / "rejected"
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
FORECAST_STATUS_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
FORECAST_STATUS_INFERENCE_NOT_RUN = "INFERENCE_NOT_RUN"
HEURISTIC_ESTIMATE_NOT_ML = "HEURISTIC_ESTIMATE_NOT_ML"
MODEL_STATE_TRAINED_CANDIDATE = "TRAINED_CANDIDATE"
MODEL_STATE_VALIDATED = "VALIDATED"
MODEL_STATE_SHADOW = "SHADOW"
MODEL_STATE_PAPER_APPROVED = "PAPER_APPROVED"
MODEL_STATE_ACTIVE = "ACTIVE"
MODEL_STATE_RETIRED = "RETIRED"
MODEL_STATE_REJECTED = "REJECTED"
MIN_MARKET_FORECAST_OOS_SELECTED_TRADES = 1
MODEL_LIFECYCLE_STATES = (
    MODEL_STATE_TRAINED_CANDIDATE,
    MODEL_STATE_VALIDATED,
    MODEL_STATE_SHADOW,
    MODEL_STATE_PAPER_APPROVED,
    MODEL_STATE_ACTIVE,
    MODEL_STATE_RETIRED,
    MODEL_STATE_REJECTED,
)
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


@dataclass(frozen=True)
class ApprovedForecastArtifact:
    symbol: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class MarketForecastAlgorithmSignalContract:
    strategy_id: str
    strategy_version: str
    signal: str
    confidence_or_setup_quality: float
    family: str
    eligibility: bool
    reason_codes: tuple[str, ...]
    regime_compatibility: float


ForecastPrediction = dict[str, Any]


class MarketForecastService:
    """Authoritative market forecast boundary for live, paper, replay, backtest, and monitoring."""

    def predict(
        self,
        candles: list[dict[str, Any]],
        *,
        microstructure_rows: list[dict[str, Any]] | None = None,
        spread: float | None = None,
        slippage: float = 0.02,
        fees: float = 0.0,
    ) -> dict[str, Any]:
        return _market_forecast_prediction(
            candles,
            microstructure_rows=microstructure_rows,
            spread=spread,
            slippage=slippage,
            fees=fees,
        )

    def select_approved_forecast_artifact(self, symbol: str) -> ApprovedForecastArtifact | None:
        return select_approved_forecast_artifact(symbol)

    def approved_model(self, symbol: str) -> dict[str, Any] | None:
        artifact = self.select_approved_forecast_artifact(symbol)
        return artifact.payload if artifact else None

    def runtime_status(self, symbol: str = "SPY") -> dict[str, Any]:
        return model_runtime_status(symbol)

    def artifact_ready(self, symbol: str, end_date: str) -> tuple[bool, str, dict[str, Any] | None]:
        artifact = self.approved_model(symbol)
        if not artifact:
            return False, f"{MODEL_VERSION} approved forecast model is unavailable.", load_market_forecast_artifact(symbol)
        artifact_end = str(((artifact.get("dateRange") or {}).get("endDate")) or "")[:10]
        if artifact_end and artifact_end < end_date:
            return False, f"Approved forecast artifact ends at {artifact_end}; needs {end_date}.", artifact
        return True, "Approved future market forecast model is ready.", artifact


MARKET_FORECAST_SERVICE = MarketForecastService()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def forecast_feature_schema_hash(features: dict[str, Any] | None) -> str | None:
    if not features:
        return None
    keys = sorted(flatten_forecast_features(features).keys())
    payload = json.dumps(keys, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def market_forecast_prediction(
    candles: list[dict[str, Any]],
    *,
    microstructure_rows: list[dict[str, Any]] | None = None,
    spread: float | None = None,
    slippage: float = 0.02,
    fees: float = 0.0,
) -> dict[str, Any]:
    return MARKET_FORECAST_SERVICE.predict(
        candles,
        microstructure_rows=microstructure_rows,
        spread=spread,
        slippage=slippage,
        fees=fees,
    )


def _market_forecast_prediction(
    candles: list[dict[str, Any]],
    *,
    microstructure_rows: list[dict[str, Any]] | None = None,
    spread: float | None = None,
    slippage: float = 0.02,
    fees: float = 0.0,
) -> dict[str, Any]:
    invocation_started_at = utc_now_iso()
    normalized = normalize_candles(candles)
    symbol = normalized[-1]["symbol"] if normalized else "SPY"
    invocation_id = f"{symbol}|{invocation_started_at}|{hashlib.sha256(json.dumps(normalized[-1] if normalized else {}, sort_keys=True).encode('utf-8')).hexdigest()[:12]}"
    missing_inputs = missing_runtime_inputs(symbol)
    model_status = model_runtime_status(symbol)
    approved_artifact = MARKET_FORECAST_SERVICE.select_approved_forecast_artifact(symbol)

    if len(normalized) < MIN_FEATURE_FORECAST_CANDLES:
        forecast_status = FORECAST_STATUS_MODEL_UNAVAILABLE if approved_artifact is None else "insufficient_data"
        future_price_prediction = (
            no_edge_future_price_prediction(
                normalized[-1]["close"],
                f"Need at least {MIN_FEATURE_FORECAST_CANDLES} one-minute candles for feature-based forecast",
            )
            if normalized
            else None
        )
        return {
            "forecastInvocationId": invocation_id,
            "eventTimestamp": normalized[-1]["timestamp"] if normalized else None,
            "barFinalizationTimestamp": normalized[-1]["timestamp"] if normalized else None,
            "featureReadyTimestamp": None,
            "inferenceStartTimestamp": None,
            "inferenceEndTimestamp": None,
            "decisionTimestamp": invocation_started_at,
            "orderSubmissionTimestamp": None,
            "status": forecast_status,
            "forecastStatus": forecast_status,
            "forecast_status": forecast_status,
            "symbol": normalized[-1]["symbol"] if normalized else "SPY",
            "horizonMinutes": FORECAST_HORIZON_MINUTES,
            "probabilitySuccess": None,
            "probabilityBuySuccess": None,
            "probabilitySellSuccess": None,
            "probabilityStop": None,
            "probabilityTimeout": None,
            "probability_buy": None,
            "probability_sell": None,
            "inferenceStatus": FORECAST_STATUS_INFERENCE_NOT_RUN,
            "inference": {
                "status": FORECAST_STATUS_INFERENCE_NOT_RUN,
                "probabilityBuySuccess": None,
                "probabilitySellSuccess": None,
                "probabilityTimeout": None,
            },
            "multiHorizonForecast": inactive_multi_horizon_forecast(
                normalized[-1]["close"] if normalized else None,
                status=forecast_status,
                reason="Not enough finalized one-minute candles for ML horizon inference.",
            ),
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
            "executionQuality": {
                "metric": "incremental_realized_net_value_after_execution_costs",
                "status": "not_estimated",
                "reason": "insufficient_data",
            },
            "allowed": False,
            "inferencePerformed": False,
            "inference_performed": False,
            "forecastAppliedToOrder": False,
            "forecast_applied_to_order": False,
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
            "featureSchemaHash": None,
            "topDrivers": [f"Need at least {MIN_FEATURE_FORECAST_CANDLES} one-minute candles for baseline forecast"],
            "missingInputs": missing_inputs,
            "updatedAt": invocation_started_at,
        }

    latest_microstructure = latest_microstructure_for_candle(normalized[-1], microstructure_rows or [])
    features = attach_microstructure_features(extract_market_forecast_features(normalized), latest_microstructure)
    feature_ready_at = utc_now_iso()
    if approved_artifact is None:
        return inference_not_run_forecast(
            normalized,
            features,
            missing_inputs=missing_inputs,
            spread=spread,
            slippage=slippage,
            fees=fees,
            invocation_id=invocation_id,
            invocation_started_at=invocation_started_at,
            feature_ready_at=feature_ready_at,
        )

    latest = normalized[-1]
    costs = round((spread if spread is not None else 0) + (slippage * 2) + fees, 4)
    inference_started_at = utc_now_iso()
    return run_forecast_inference(
        approved_artifact,
        {
            "features": features,
            "latest": latest,
            "costs": costs,
            "executionCostInputs": {
                "spread": spread,
                "slippage": slippage,
                "fees": fees,
            },
            "missingInputs": missing_inputs,
            "model": model_runtime_status(normalized[-1]["symbol"]),
            "forecastInvocationId": invocation_id,
            "eventTimestamp": latest["timestamp"],
            "barFinalizationTimestamp": latest["timestamp"],
            "featureReadyTimestamp": feature_ready_at,
            "inferenceStartTimestamp": inference_started_at,
        },
    )


def run_forecast_inference(artifact: ApprovedForecastArtifact, feature_snapshot: dict[str, Any]) -> ForecastPrediction:
    features = feature_snapshot["features"]
    latest = feature_snapshot["latest"]
    costs = float(feature_snapshot.get("costs") or 0.0)
    artifact_payload = artifact.payload
    ensemble = ensemble_probabilities(features, artifact_payload)
    probabilities = ensemble["probabilities"]
    buy_probability = probabilities[OUTCOME_TARGET]
    sell_probability = probabilities[OUTCOME_STOP]
    timeout_probability = probabilities[OUTCOME_TIMEOUT]
    predicted_outcome = max(probabilities.items(), key=lambda item: item[1])[0]
    barriers = volatility_adjusted_barriers(features, latest["close"], artifact=artifact_payload)
    expected_move = barriers["targetDistance"]
    expected_loss = barriers["stopDistance"]
    execution_cost_inputs = feature_snapshot.get("executionCostInputs") or {}
    conservative_execution_quality = estimate_execution_quality(
        features,
        latest,
        barriers,
        spread=execution_cost_inputs.get("spread"),
        slippage=float(execution_cost_inputs.get("slippage") if execution_cost_inputs.get("slippage") is not None else 0.02),
        fees=float(execution_cost_inputs.get("fees") if execution_cost_inputs.get("fees") is not None else 0.0),
    )
    candidate_side = DECISION_BUY if buy_probability >= sell_probability else DECISION_SELL
    execution_quality = EXECUTION_COST_MODEL_SERVICE.estimate(
        symbol=str(latest.get("symbol") or artifact.symbol),
        side=candidate_side,
        order_type=str(execution_cost_inputs.get("orderType") or "limit"),
        feature_snapshot={
            "features": features,
            "latest": latest,
            "barriers": barriers,
            "probabilities": probabilities,
            "candidateSide": candidate_side,
            "baseCosts": costs,
        },
        conservative_fallback=conservative_execution_quality,
    )
    execution_adjusted_costs = float(execution_quality["totalEstimatedCost"])
    execution_multiplier = float(execution_quality["expectedExecutionMultiplier"])
    buy_gross_expected_value = (buy_probability * expected_move) - (sell_probability * expected_loss)
    sell_gross_expected_value = (sell_probability * expected_move) - (buy_probability * expected_loss)
    buy_expected_value = round((buy_gross_expected_value * execution_multiplier) - execution_adjusted_costs, 4)
    sell_expected_value = round((sell_gross_expected_value * execution_multiplier) - execution_adjusted_costs, 4)
    market_regime = market_regime_profile(features)
    future_price_prediction = forecast_future_price_prediction(
        features,
        latest["close"],
        probabilities=probabilities,
        barriers=barriers,
        market_regime=market_regime,
    )
    multi_horizon_forecast = build_multi_horizon_forecast(
        artifact,
        feature_snapshot,
        execution_cost_inputs=execution_cost_inputs,
        primary_probabilities=probabilities,
        primary_barriers=barriers,
        primary_market_regime=market_regime,
    )
    regime_allows = regime_allows_forecast(features, market_regime)
    base_threshold = forecast_probability_threshold(artifact_payload)
    decision = forecast_trade_decision(
        probabilities,
        buy_expected_value=buy_expected_value,
        sell_expected_value=sell_expected_value,
        regime_allows=regime_allows,
        market_regime=market_regime,
        uncertainty=ensemble,
        features=features,
        base_threshold=base_threshold,
        execution_quality=execution_quality,
    )
    inference_ended_at = utc_now_iso()

    return {
        "forecastInvocationId": feature_snapshot.get("forecastInvocationId"),
        "eventTimestamp": feature_snapshot.get("eventTimestamp"),
        "barFinalizationTimestamp": feature_snapshot.get("barFinalizationTimestamp"),
        "featureReadyTimestamp": feature_snapshot.get("featureReadyTimestamp"),
        "inferenceStartTimestamp": feature_snapshot.get("inferenceStartTimestamp"),
        "inferenceEndTimestamp": inference_ended_at,
        "decisionTimestamp": inference_ended_at,
        "orderSubmissionTimestamp": None,
        "status": "ready",
        "forecastStatus": "ready",
        "forecast_status": "ready",
        "symbol": latest["symbol"],
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "probabilitySuccess": round(buy_probability, 4),
        "probabilityBuySuccess": round(buy_probability, 4),
        "probabilitySellSuccess": round(sell_probability, 4),
        "probabilityStop": round(sell_probability, 4),
        "probabilityTimeout": round(timeout_probability, 4),
        "artifactId": artifact_payload.get("artifactId"),
        "featureSchemaHash": (
            artifact_payload.get("featureSchemaHash")
            or ((artifact_payload.get("validationDeploymentParity") or {}).get("featureSchemaHash"))
            or forecast_feature_schema_hash(features)
        ),
        "inferenceStatus": "ready",
        "inference": {
            "status": "ready",
            "probabilityBuySuccess": round(buy_probability, 4),
            "probabilitySellSuccess": round(sell_probability, 4),
            "probabilityTimeout": round(timeout_probability, 4),
        },
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
        "buyGrossExpectedValueBeforeExecution": round(buy_gross_expected_value, 4),
        "sellGrossExpectedValueBeforeExecution": round(sell_gross_expected_value, 4),
        "incrementalExpectedNetValueAfterExecutionCosts": decision["expectedValue"],
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
        "multiHorizonForecast": multi_horizon_forecast,
        "costs": execution_adjusted_costs,
        "baseCosts": costs,
        "executionQuality": execution_quality,
        "allowed": decision["action"] in {DECISION_BUY, DECISION_SELL},
        "forecastAppliedToOrder": decision["action"] in {DECISION_BUY, DECISION_SELL},
        "forecast_applied_to_order": decision["action"] in {DECISION_BUY, DECISION_SELL},
        "inferencePerformed": True,
        "inference_performed": True,
        "model": feature_snapshot.get("model") or model_runtime_status(artifact.symbol),
        "regime": forecast_regime(features),
        "marketRegime": market_regime,
        "algorithmSignals": algorithm_signal_summary(features),
        "uncertainty": ensemble,
        "features": features,
        "topDrivers": forecast_drivers(features, probabilities, decision, regime_allows),
        "missingInputs": feature_snapshot.get("missingInputs") or [],
        "updatedAt": datetime.utcnow().isoformat() + "Z",
    }


def build_multi_horizon_forecast(
    artifact: ApprovedForecastArtifact,
    feature_snapshot: dict[str, Any],
    *,
    execution_cost_inputs: dict[str, Any],
    primary_probabilities: dict[str, float],
    primary_barriers: dict[str, float],
    primary_market_regime: dict[str, Any],
) -> dict[str, Any]:
    features = feature_snapshot["features"]
    latest = feature_snapshot["latest"]
    artifact_payload = artifact.payload
    horizons = [
        horizon_forecast_row(
            artifact,
            feature_snapshot,
            horizon_minutes=horizon,
            execution_cost_inputs=execution_cost_inputs,
            primary_probabilities=primary_probabilities,
            primary_barriers=primary_barriers,
            primary_market_regime=primary_market_regime,
        )
        for horizon in MARKET_FORECAST_POSITION_HORIZONS_MINUTES
    ]
    ready_count = sum(1 for row in horizons if row["status"] == "ready")
    return {
        "status": "ready" if ready_count else FORECAST_STATUS_MODEL_UNAVAILABLE,
        "forecastStatus": "ready" if ready_count else FORECAST_STATUS_MODEL_UNAVAILABLE,
        "activationPolicy": MULTI_HORIZON_POSITION_FORECAST_POLICY,
        "positionManagementAuthority": "advisory_only",
        "entryAuthorization": False,
        "forecastAppliedToOrder": False,
        "positionManagementAppliedToOrder": False,
        "horizons": horizons,
        "summary": multi_horizon_summary(horizons),
        "latestPrice": round(float(latest["close"]), 4),
        "featureSchemaHash": forecast_feature_schema_hash(features),
        "artifactId": artifact_payload.get("artifactId"),
        "supportedHorizonsMinutes": list(MARKET_FORECAST_POSITION_HORIZONS_MINUTES),
    }


def horizon_forecast_row(
    artifact: ApprovedForecastArtifact,
    feature_snapshot: dict[str, Any],
    *,
    horizon_minutes: int,
    execution_cost_inputs: dict[str, Any],
    primary_probabilities: dict[str, float],
    primary_barriers: dict[str, float],
    primary_market_regime: dict[str, Any],
) -> dict[str, Any]:
    features = feature_snapshot["features"]
    latest = feature_snapshot["latest"]
    horizon_payload = horizon_model_payload(artifact.payload, horizon_minutes)
    if horizon_payload is None:
        return inactive_horizon_row(
            latest["close"],
            horizon_minutes=horizon_minutes,
            status=FORECAST_STATUS_MODEL_UNAVAILABLE,
            reason=f"No approved ML horizon head is available for {horizon_minutes} minutes.",
        )

    if horizon_minutes == FORECAST_HORIZON_MINUTES and horizon_payload is artifact.payload:
        ensemble = uncertainty_summary([{"name": "primary_5m_ml", "probabilities": primary_probabilities}])
        probabilities = primary_probabilities
        barriers = primary_barriers
        market_regime = primary_market_regime
    else:
        ensemble = ensemble_probabilities(features, horizon_payload)
        probabilities = ensemble["probabilities"]
        barriers = volatility_adjusted_barriers(features, latest["close"], artifact=horizon_payload, horizon_minutes=horizon_minutes)
        market_regime = market_regime_profile(features)

    buy_probability = float(probabilities[OUTCOME_TARGET])
    sell_probability = float(probabilities[OUTCOME_STOP])
    timeout_probability = float(probabilities[OUTCOME_TIMEOUT])
    execution_quality = estimate_execution_quality(
        features,
        latest,
        barriers,
        spread=execution_cost_inputs.get("spread"),
        slippage=float(execution_cost_inputs.get("slippage") if execution_cost_inputs.get("slippage") is not None else 0.02),
        fees=float(execution_cost_inputs.get("fees") if execution_cost_inputs.get("fees") is not None else 0.0),
        horizon_minutes=horizon_minutes,
    )
    execution_cost = float(execution_quality["totalEstimatedCost"])
    execution_multiplier = float(execution_quality["expectedExecutionMultiplier"])
    buy_ev = round(((buy_probability * barriers["targetDistance"]) - (sell_probability * barriers["stopDistance"])) * execution_multiplier - execution_cost, 4)
    sell_ev = round(((sell_probability * barriers["targetDistance"]) - (buy_probability * barriers["stopDistance"])) * execution_multiplier - execution_cost, 4)
    threshold = forecast_probability_threshold(horizon_payload)
    edge_gap = abs(buy_probability - sell_probability)
    minimum_edge_gap = DEFAULT_MIN_EDGE_GAP + (0.03 if market_regime.get("trend") == "sideways" else 0.0)
    future_price_prediction = forecast_future_price_prediction(
        features,
        latest["close"],
        probabilities=probabilities,
        barriers=barriers,
        market_regime=market_regime,
        horizon_minutes=horizon_minutes,
    )
    advice = multi_horizon_position_advice(
        buy_probability=buy_probability,
        sell_probability=sell_probability,
        timeout_probability=timeout_probability,
        buy_expected_value=buy_ev,
        sell_expected_value=sell_ev,
        threshold=threshold,
        minimum_edge_gap=minimum_edge_gap,
    )
    return {
        "status": "ready",
        "forecastStatus": "ready",
        "horizonMinutes": horizon_minutes,
        "modelApplied": True,
        "modelKind": horizon_payload.get("modelKind") or artifact.payload.get("modelKind") or "unknown",
        "artifactId": horizon_payload.get("artifactId") or artifact.payload.get("artifactId"),
        "featureSchemaHash": (
            horizon_payload.get("featureSchemaHash")
            or artifact.payload.get("featureSchemaHash")
            or forecast_feature_schema_hash(features)
        ),
        "probabilityBuySuccess": round(buy_probability, 4),
        "probabilitySellSuccess": round(sell_probability, 4),
        "probabilityTimeout": round(timeout_probability, 4),
        "probabilityUp": round(buy_probability, 4),
        "probabilityDown": round(sell_probability, 4),
        "probabilityFlatOrNoEdge": round(timeout_probability, 4),
        "threshold": round(threshold, 4),
        "edgeGap": round(edge_gap, 4),
        "minimumEdgeGap": round(minimum_edge_gap, 4),
        "buyExpectedValue": buy_ev,
        "sellExpectedValue": sell_ev,
        "futurePricePrediction": future_price_prediction,
        "predictedDirection": future_price_prediction["direction"],
        "predictedPrice": future_price_prediction["predictedPrice"],
        "predictedChangeDollars": future_price_prediction["predictedChangeDollars"],
        "expectedExecutionCost": round(execution_cost, 4),
        "executionQuality": execution_quality,
        "uncertainty": ensemble,
        "advice": advice,
        "entryAuthorization": False,
        "forecastAppliedToOrder": False,
        "positionManagementAppliedToOrder": False,
        "activationPolicy": MULTI_HORIZON_POSITION_FORECAST_POLICY,
    }


def horizon_model_payload(artifact_payload: dict[str, Any], horizon_minutes: int) -> dict[str, Any] | None:
    containers = (
        artifact_payload.get("horizonModels"),
        artifact_payload.get("multiHorizonModels"),
        (artifact_payload.get("multiHorizonForecast") or {}).get("horizonModels")
        if isinstance(artifact_payload.get("multiHorizonForecast"), dict)
        else None,
    )
    for container in containers:
        if not isinstance(container, dict):
            continue
        payload = container.get(str(horizon_minutes)) or container.get(horizon_minutes) or container.get(f"{horizon_minutes}m")
        if isinstance(payload, dict) and payload.get("approved", True) is not False:
            return {**artifact_payload, **payload, "horizonMinutes": horizon_minutes}
    if horizon_minutes == FORECAST_HORIZON_MINUTES:
        return artifact_payload
    return None


def multi_horizon_position_advice(
    *,
    buy_probability: float,
    sell_probability: float,
    timeout_probability: float,
    buy_expected_value: float,
    sell_expected_value: float,
    threshold: float,
    minimum_edge_gap: float,
) -> dict[str, Any]:
    up_edge = buy_probability - sell_probability
    down_edge = sell_probability - buy_probability
    up_confirmed = buy_probability >= threshold and up_edge >= minimum_edge_gap and buy_expected_value > 0
    down_confirmed = sell_probability >= threshold and down_edge >= minimum_edge_gap and sell_expected_value > 0
    no_edge = timeout_probability >= max(buy_probability, sell_probability)
    return {
        "longPosition": "KEEP" if up_confirmed else "CLOSE_REVIEW" if down_confirmed or buy_expected_value < 0 else "MONITOR",
        "shortPosition": "KEEP" if down_confirmed else "CLOSE_REVIEW" if up_confirmed or sell_expected_value < 0 else "MONITOR",
        "newLongEntry": "CONSIDER_AFTER_STRATEGY_SIGNAL" if up_confirmed else "WAIT",
        "newShortEntry": "CONSIDER_AFTER_STRATEGY_SIGNAL" if down_confirmed else "WAIT",
        "flatMarket": "WAIT" if no_edge else "DIRECTIONAL_EDGE_PRESENT",
        "reasonCodes": multi_horizon_reason_codes(
            up_confirmed=up_confirmed,
            down_confirmed=down_confirmed,
            no_edge=no_edge,
            buy_expected_value=buy_expected_value,
            sell_expected_value=sell_expected_value,
        ),
    }


def multi_horizon_reason_codes(
    *,
    up_confirmed: bool,
    down_confirmed: bool,
    no_edge: bool,
    buy_expected_value: float,
    sell_expected_value: float,
) -> list[str]:
    if up_confirmed:
        return ["ml_horizon_up_edge_confirmed", "long_position_keep_bias", "new_short_wait_bias"]
    if down_confirmed:
        return ["ml_horizon_down_edge_confirmed", "long_position_close_review_bias", "new_long_wait_bias"]
    reasons = ["ml_horizon_edge_not_confirmed"]
    if no_edge:
        reasons.append("timeout_or_no_edge_probability_dominant")
    if buy_expected_value <= 0:
        reasons.append("long_expected_value_not_positive_after_execution_costs")
    if sell_expected_value <= 0:
        reasons.append("short_expected_value_not_positive_after_execution_costs")
    return reasons


def multi_horizon_summary(horizons: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [row for row in horizons if row.get("status") == "ready"]
    if not ready:
        return {
            "primaryBias": "MODEL_UNAVAILABLE",
            "longPosition": "NO_ML_ADVICE",
            "newLongEntry": "WAIT_FOR_VALIDATED_MODEL",
            "readyHorizons": 0,
        }
    up_count = sum(1 for row in ready if row.get("advice", {}).get("longPosition") == "KEEP")
    down_count = sum(1 for row in ready if row.get("advice", {}).get("shortPosition") == "KEEP")
    wait_count = sum(1 for row in ready if row.get("advice", {}).get("newLongEntry") == "WAIT")
    if up_count > down_count:
        bias = "UP"
    elif down_count > up_count:
        bias = "DOWN"
    else:
        bias = "MIXED"
    return {
        "primaryBias": bias,
        "longPosition": "KEEP" if up_count >= max(1, len(ready) // 2) else "CLOSE_REVIEW" if down_count >= max(1, len(ready) // 2) else "MONITOR",
        "shortPosition": "KEEP" if down_count >= max(1, len(ready) // 2) else "CLOSE_REVIEW" if up_count >= max(1, len(ready) // 2) else "MONITOR",
        "newLongEntry": "WAIT" if wait_count >= max(1, len(ready) // 2) else "CONSIDER_AFTER_STRATEGY_SIGNAL",
        "readyHorizons": len(ready),
    }


def inactive_multi_horizon_forecast(latest_close: Any, *, status: str, reason: str) -> dict[str, Any]:
    horizons = [
        inactive_horizon_row(latest_close, horizon_minutes=horizon, status=status, reason=reason)
        for horizon in MARKET_FORECAST_POSITION_HORIZONS_MINUTES
    ]
    return {
        "status": status,
        "forecastStatus": status,
        "activationPolicy": MULTI_HORIZON_POSITION_FORECAST_POLICY,
        "positionManagementAuthority": "advisory_only",
        "entryAuthorization": False,
        "forecastAppliedToOrder": False,
        "positionManagementAppliedToOrder": False,
        "horizons": horizons,
        "summary": multi_horizon_summary(horizons),
        "supportedHorizonsMinutes": list(MARKET_FORECAST_POSITION_HORIZONS_MINUTES),
        "reason": reason,
    }


def inactive_horizon_row(latest_close: Any, *, horizon_minutes: int, status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "forecastStatus": status,
        "horizonMinutes": horizon_minutes,
        "modelApplied": False,
        "probabilityBuySuccess": None,
        "probabilitySellSuccess": None,
        "probabilityTimeout": None,
        "probabilityUp": None,
        "probabilityDown": None,
        "probabilityFlatOrNoEdge": None,
        "threshold": None,
        "edgeGap": None,
        "minimumEdgeGap": None,
        "buyExpectedValue": None,
        "sellExpectedValue": None,
        "futurePricePrediction": no_edge_future_price_prediction(latest_close or 0.0, reason, horizon_minutes=horizon_minutes),
        "predictedDirection": "unavailable",
        "predictedPrice": None,
        "predictedChangeDollars": None,
        "advice": {
            "longPosition": "NO_ML_ADVICE",
            "shortPosition": "NO_ML_ADVICE",
            "newLongEntry": "WAIT_FOR_VALIDATED_MODEL",
            "newShortEntry": "WAIT_FOR_VALIDATED_MODEL",
            "flatMarket": "WAIT_FOR_VALIDATED_MODEL",
            "reasonCodes": ["ml_horizon_model_unavailable"],
        },
        "entryAuthorization": False,
        "forecastAppliedToOrder": False,
        "positionManagementAppliedToOrder": False,
        "activationPolicy": MULTI_HORIZON_POSITION_FORECAST_POLICY,
        "reason": reason,
    }


def inference_not_run_forecast(
    normalized: list[dict[str, Any]],
    features: dict[str, Any],
    *,
    missing_inputs: list[str],
    spread: float | None,
    slippage: float,
    fees: float,
    invocation_id: str,
    invocation_started_at: str,
    feature_ready_at: str,
) -> dict[str, Any]:
    latest = normalized[-1]
    costs = round((spread if spread is not None else 0) + (slippage * 2) + fees, 4)
    heuristic_ensemble = fallback_ensemble_probabilities(features)
    heuristic_probabilities = heuristic_ensemble["probabilities"]
    heuristic_barriers = volatility_adjusted_barriers(features, latest["close"], artifact=None)
    heuristic_buy_probability = heuristic_probabilities[OUTCOME_TARGET]
    heuristic_sell_probability = heuristic_probabilities[OUTCOME_STOP]
    heuristic_timeout_probability = heuristic_probabilities[OUTCOME_TIMEOUT]
    heuristic_buy_expected_value = round(
        (heuristic_buy_probability * heuristic_barriers["targetDistance"])
        - (heuristic_sell_probability * heuristic_barriers["stopDistance"])
        - costs,
        4,
    )
    heuristic_sell_expected_value = round(
        (heuristic_sell_probability * heuristic_barriers["targetDistance"])
        - (heuristic_buy_probability * heuristic_barriers["stopDistance"])
        - costs,
        4,
    )
    market_regime = market_regime_profile(features)
    regime_allows = regime_allows_forecast(features, market_regime)
    heuristic_decision = forecast_trade_decision(
        heuristic_probabilities,
        buy_expected_value=heuristic_buy_expected_value,
        sell_expected_value=heuristic_sell_expected_value,
        regime_allows=regime_allows,
        market_regime=market_regime,
        uncertainty=heuristic_ensemble,
        features=features,
        base_threshold=DEFAULT_SUCCESS_THRESHOLD,
    )
    heuristic_future_price_prediction = forecast_future_price_prediction(
        features,
        latest["close"],
        probabilities=heuristic_probabilities,
        barriers=heuristic_barriers,
        market_regime=market_regime,
    )
    unavailable_reason = "No explicitly approved market-forecast model is loaded; heuristic estimate is UI diagnostics only."
    return {
        "forecastInvocationId": invocation_id,
        "eventTimestamp": latest["timestamp"],
        "barFinalizationTimestamp": latest["timestamp"],
        "featureReadyTimestamp": feature_ready_at,
        "inferenceStartTimestamp": None,
        "inferenceEndTimestamp": None,
        "decisionTimestamp": feature_ready_at,
        "orderSubmissionTimestamp": None,
        "status": FORECAST_STATUS_MODEL_UNAVAILABLE,
        "forecastStatus": FORECAST_STATUS_MODEL_UNAVAILABLE,
        "forecast_status": FORECAST_STATUS_MODEL_UNAVAILABLE,
        "symbol": latest["symbol"],
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "probabilitySuccess": None,
        "probabilityBuySuccess": None,
        "probabilitySellSuccess": None,
        "probabilityStop": None,
        "probabilityTimeout": None,
        "probability_buy": None,
        "probability_sell": None,
        "artifactId": None,
        "featureSchemaHash": forecast_feature_schema_hash(features),
        "inferenceStatus": FORECAST_STATUS_INFERENCE_NOT_RUN,
        "inference": {
            "status": FORECAST_STATUS_INFERENCE_NOT_RUN,
            "probabilityBuySuccess": None,
            "probabilitySellSuccess": None,
            "probabilityTimeout": None,
        },
        "multiHorizonForecast": inactive_multi_horizon_forecast(
            latest["close"],
            status=FORECAST_STATUS_MODEL_UNAVAILABLE,
            reason=unavailable_reason,
        ),
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
            "expectedValue": None,
            "positionSizeMultiplier": 0.0,
            "reasons": [unavailable_reason],
        },
        "threshold": DEFAULT_SUCCESS_THRESHOLD,
        "minimumEdgeGap": DEFAULT_MIN_EDGE_GAP,
        "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
        "expectedValue": None,
        "buyExpectedValue": None,
        "sellExpectedValue": None,
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
        "futurePricePrediction": no_edge_future_price_prediction(latest["close"], unavailable_reason),
        "costs": costs,
        "executionQuality": {
            "metric": "incremental_realized_net_value_after_execution_costs",
            "status": "not_estimated",
            "reason": "model_unavailable",
        },
        "allowed": False,
        "inferencePerformed": False,
        "inference_performed": False,
        "forecastAppliedToOrder": False,
        "forecast_applied_to_order": False,
        "model": model_runtime_status(latest["symbol"]),
        "regime": forecast_regime(features),
        "marketRegime": {
            **market_regime,
            "allowedLong": False,
            "allowedShort": False,
            "positionSizeMultiplier": 0.0,
            "notes": [*list(market_regime.get("notes") or []), unavailable_reason],
        },
        "algorithmSignals": algorithm_signal_summary(features),
        "uncertainty": {
            "modelCount": 0,
            "modelDisagreement": None,
            "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
            "members": [],
        },
        "features": features,
        "topDrivers": [unavailable_reason, HEURISTIC_ESTIMATE_NOT_ML],
        "missingInputs": missing_inputs,
        "heuristicEstimate": {
            "status": HEURISTIC_ESTIMATE_NOT_ML,
            "forecastAppliedToOrder": False,
            "forecast_applied_to_order": False,
            "probabilityBuySuccess": round(heuristic_buy_probability, 4),
            "probabilitySellSuccess": round(heuristic_sell_probability, 4),
            "probabilityTimeout": round(heuristic_timeout_probability, 4),
            "probability_buy": round(heuristic_buy_probability, 4),
            "probability_sell": round(heuristic_sell_probability, 4),
            "decision": heuristic_decision,
            "buyExpectedValue": heuristic_buy_expected_value,
            "sellExpectedValue": heuristic_sell_expected_value,
            "barriers": {
                "targetDistance": round(heuristic_barriers["targetDistance"], 4),
                "stopDistance": round(heuristic_barriers["stopDistance"], 4),
                "minTargetPct": heuristic_barriers["minTargetPct"],
                "minStopPct": heuristic_barriers["minStopPct"],
                "targetAtrMultiplier": heuristic_barriers["targetAtrMultiplier"],
                "stopAtrMultiplier": heuristic_barriers["stopAtrMultiplier"],
                "fixedTargetDollars": heuristic_barriers["fixedTargetDollars"],
                "fixedStopDollars": heuristic_barriers["fixedStopDollars"],
                "atr5m": round(heuristic_barriers["atr5m"], 4),
            },
            "futurePricePrediction": heuristic_future_price_prediction,
            "uncertainty": heuristic_ensemble,
            "topDrivers": forecast_drivers(features, heuristic_probabilities, heuristic_decision, regime_allows),
        },
        "updatedAt": invocation_started_at,
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
        "intervalMinutes": 1,
        "recordingPolicy": "every_forecast_invocation_from_finalized_one_minute_events",
        "dashboardAggregationPolicy": "five_minute_rows_are_derived_after_authoritative_event_logging",
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
    invocation_id = str(forecast.get("forecastInvocationId") or f"{symbol.upper()}|{feed}|{timeframe}|{timestamp}|{utc_now_iso()}")
    barriers = forecast.get("barriers") or {}
    decision = forecast.get("decision") or {}
    outcome = forecast.get("outcome") or {}
    probabilities = {
        "probabilityBuySuccess": forecast.get("probabilityBuySuccess"),
        "probabilitySellSuccess": forecast.get("probabilitySellSuccess"),
        "probabilityTimeout": forecast.get("probabilityTimeout"),
        "outcomeProbabilities": outcome.get("probabilities"),
    }
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
            "id": f"{symbol.upper()}|{feed}|{timeframe}|{timestamp}|{MODEL_VERSION}|{safe_artifact_id(invocation_id)}",
            "forecastInvocationId": invocation_id,
            "symbol": symbol.upper(),
            "feed": feed,
            "timeframe": timeframe,
            "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
            "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
            "ledgerRule": FUTURE_MARKET_PREDICTION_LEDGER_RULE,
            "recordingPolicy": "every_forecast_invocation_from_finalized_one_minute_events",
            "modelVersion": MODEL_VERSION,
            "artifactId": forecast.get("artifactId"),
            "featureSchemaHash": forecast.get("featureSchemaHash"),
            "eventTimestamp": forecast.get("eventTimestamp") or timestamp,
            "barFinalizationTimestamp": forecast.get("barFinalizationTimestamp") or timestamp,
            "featureReadyTimestamp": forecast.get("featureReadyTimestamp"),
            "inferenceStartTimestamp": forecast.get("inferenceStartTimestamp"),
            "inferenceEndTimestamp": forecast.get("inferenceEndTimestamp"),
            "decisionTimestamp": forecast.get("decisionTimestamp") or forecast.get("updatedAt"),
            "orderSubmissionTimestamp": forecast.get("orderSubmissionTimestamp"),
            "predictionTimestamp": timestamp,
            "generatedAt": forecast.get("updatedAt") or datetime.utcnow().isoformat() + "Z",
            "horizonMinutes": int(forecast.get("horizonMinutes") or FORECAST_HORIZON_MINUTES),
            "entryPrice": round(float(latest["close"]), 4),
            "predictionMarket": candle_market_snapshot(latest),
            "predictionProbabilities": probabilities,
            "prediction": {
                "status": forecast.get("status"),
                "forecastStatus": forecast.get("forecastStatus") or forecast.get("forecast_status") or forecast.get("status"),
                "forecastAppliedToOrder": bool(forecast.get("forecastAppliedToOrder") or forecast.get("forecast_applied_to_order")),
                "heuristicStatus": (forecast.get("heuristicEstimate") or {}).get("status"),
                "predictedOutcome": outcome.get("predicted"),
                **probabilities,
                "decisionAction": decision.get("action"),
                "candidateAction": decision.get("candidateAction"),
                "confidence": decision.get("confidence"),
                "edgeGap": decision.get("edgeGap"),
                "expectedValue": decision.get("expectedValue"),
                "expectedValueMetric": decision.get("expectedValueMetric"),
                "buyExpectedValue": forecast.get("buyExpectedValue"),
                "sellExpectedValue": forecast.get("sellExpectedValue"),
                "incrementalExpectedNetValueAfterExecutionCosts": forecast.get("incrementalExpectedNetValueAfterExecutionCosts"),
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
            "multiHorizonForecast": forecast.get("multiHorizonForecast") or {},
            "priceComparison": price_comparison_snapshot(latest, predicted_future),
            "expectedCosts": {
                "costs": numeric(forecast.get("costs")),
                "baseCosts": numeric(forecast.get("baseCosts")),
                "executionQuality": forecast.get("executionQuality") or {},
            },
            "costs": numeric(forecast.get("costs")),
            "baseCosts": numeric(forecast.get("baseCosts")),
            "executionQuality": forecast.get("executionQuality") or {},
            "marketRegime": forecast.get("marketRegime") or {},
            "regime": forecast.get("regime") or {},
            "algorithmSignals": forecast.get("algorithmSignals") or {},
            "uncertainty": forecast.get("uncertainty") or {},
            "features": forecast.get("features") or {},
            "actual": {
                "status": "pending",
                "outcome": None,
                "realizedOutcome": None,
                "actualFill": {
                    "status": "pending",
                    "orderSubmissionTimestamp": forecast.get("orderSubmissionTimestamp"),
                    "fillTimestamp": None,
                    "filledQuantity": None,
                    "averageFillPrice": None,
                    "partialFillFraction": None,
                },
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
    estimated_execution_cost = numeric((record.get("executionQuality") or {}).get("totalEstimatedCost")) or costs
    buy_value = directional_resolution_value(buy_resolution, entry, float(future[-1]["close"]), target_distance, stop_distance)
    sell_value = directional_resolution_value(sell_resolution, entry, float(future[-1]["close"]), target_distance, stop_distance)
    realized_execution = realized_execution_cost_snapshot(
        record,
        candles,
        index,
        action=action,
        candidate_action=candidate_action,
        entry=entry,
        exit_price=exit_price,
        estimated_execution_cost=estimated_execution_cost,
        stop_distance=stop_distance,
    )
    realized_execution_cost = float(realized_execution["realizedExecutionCost"])
    horizon_candle = future[horizon - 1] if len(future) >= horizon else future[-1]
    horizon_close = float(horizon_candle["close"])
    max_high = max(float(candle["high"]) for candle in future)
    min_low = min(float(candle["low"]) for candle in future)
    price_comparison = resolved_price_comparison_snapshot(record, horizon_candle, entry)
    actual = {
        "status": "resolved",
        "outcome": outcome,
        "realizedOutcome": outcome,
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
        "actualFill": {
            "status": realized_execution.get("fillStatus"),
            "orderSubmissionTimestamp": record.get("orderSubmissionTimestamp"),
            "fillTimestamp": hit_at if action in {DECISION_BUY, DECISION_SELL} else None,
            "filledQuantity": None,
            "averageFillPrice": realized_execution.get("actualExecutableEntryPrice"),
            "partialFillFraction": realized_execution.get("partialFillFraction"),
            "realizedExecutionCost": realized_execution.get("realizedExecutionCost"),
        },
        "estimatedExecutionCost": round(estimated_execution_cost, 4),
        "realizedExecution": realized_execution,
        "realizedDecisionValueDollars": round(realized_decision_value(action, buy_value, sell_value, costs), 4),
        "incrementalRealizedNetValueAfterExecutionCosts": round(
            realized_decision_value(action, buy_value, sell_value, realized_execution_cost),
            4,
        ),
        "realizedVersusEstimatedCostError": round(realized_execution_cost - estimated_execution_cost, 4),
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


def realized_execution_cost_snapshot(
    record: dict[str, Any],
    candles: list[dict[str, Any]],
    decision_index: int,
    *,
    action: str,
    candidate_action: str,
    entry: float,
    exit_price: float,
    estimated_execution_cost: float,
    stop_distance: float,
) -> dict[str, Any]:
    if action not in {DECISION_BUY, DECISION_SELL}:
        return {
            "status": "no_order",
            "fillStatus": "not_submitted",
            "fillProbabilityRealized": 0.0,
            "partialFillFraction": 0.0,
            "actualExecutableEntryPrice": None,
            "entryPriceSlippage": 0.0,
            "exitPrice": None,
            "estimatedExecutionCost": round(estimated_execution_cost, 6),
            "realizedExecutionCost": 0.0,
            "realizedVersusEstimatedCostError": round(-estimated_execution_cost, 6),
            "metric": "incremental_realized_net_value_after_execution_costs",
        }
    executable = candles[decision_index + 1] if decision_index + 1 < len(candles) else candles[decision_index]
    executable_entry = float(executable.get("open") or executable.get("close") or entry)
    if action == DECISION_BUY:
        entry_slippage = max(0.0, executable_entry - entry)
    else:
        entry_slippage = max(0.0, entry - executable_entry)
    exit_slippage = 0.0
    if candidate_action in {DECISION_BUY, DECISION_SELL}:
        predicted_cost = numeric(record.get("baseCosts")) or numeric(record.get("costs"))
    else:
        predicted_cost = numeric(record.get("costs"))
    stop_limit_miss_probability = numeric((record.get("executionQuality") or {}).get("stopLimitMissProbability"))
    stop_limit_miss_realized_cost = stop_distance * stop_limit_miss_probability * 0.25
    realized_execution_cost = max(0.0, predicted_cost + entry_slippage + exit_slippage + stop_limit_miss_realized_cost)
    return {
        "status": "resolved",
        "fillStatus": "filled",
        "fillProbabilityRealized": 1.0,
        "partialFillFraction": 1.0,
        "actualExecutableEntryPrice": round(executable_entry, 4),
        "entryPriceSlippage": round(entry_slippage, 6),
        "exitPrice": round(exit_price, 4),
        "exitPriceSlippage": round(exit_slippage, 6),
        "stopLimitMissRealizedCost": round(stop_limit_miss_realized_cost, 6),
        "estimatedExecutionCost": round(estimated_execution_cost, 6),
        "realizedExecutionCost": round(realized_execution_cost, 6),
        "realizedVersusEstimatedCostError": round(realized_execution_cost - estimated_execution_cost, 6),
        "metric": "incremental_realized_net_value_after_execution_costs",
    }


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
        "intervalMinutes": 1,
        "recordingPolicy": "every_forecast_invocation_from_finalized_one_minute_events",
        "dashboardAggregationPolicy": "five_minute_rows_are_derived_after_authoritative_event_logging",
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
    contracts = market_forecast_algorithm_signal_contracts(
        candles,
        atr_value=atr_value,
        regime_profile=regime_profile,
        rsi_value=rsi_value,
        adx_value=adx_value,
    )
    family_scores = algorithm_contract_family_scores(contracts, regime_profile=regime_profile)
    weighted = algorithm_contract_scores(contracts)
    buy_score = weighted["buy"]
    sell_score = weighted["sell"]
    hold_score = weighted["hold"]
    sorted_scores = sorted([buy_score, sell_score, hold_score], reverse=True)
    winner_margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) >= 2 else 0.0
    disagreement = 1.0 - winner_margin
    features: dict[str, Any] = {
        "contract_schema_version": 1,
        "contract_count": len(contracts),
        "eligible_contract_count": sum(1 for contract in contracts if contract.eligibility),
        "buy_contract_count": sum(1 for contract in contracts if contract.signal == "Buy"),
        "sell_contract_count": sum(1 for contract in contracts if contract.signal == "Sell"),
        "hold_contract_count": sum(1 for contract in contracts if contract.signal == "Hold"),
        **family_scores,
        "weighted_buy_score": buy_score,
        "weighted_sell_score": sell_score,
        "weighted_hold_score": hold_score,
        "buy_minus_sell_score": buy_score - sell_score,
        "winner_score_margin": winner_margin,
        "algorithm_disagreement": disagreement,
    }
    for contract in contracts:
        prefix = f"strategy__{contract.strategy_id}"
        features[f"{prefix}__signal"] = signal_value(contract.signal)
        features[f"{prefix}__confidence_or_setup_quality"] = contract.confidence_or_setup_quality
        features[f"{prefix}__eligible"] = contract.eligibility
        features[f"{prefix}__regime_compatibility"] = contract.regime_compatibility
        features[f"{prefix}__version_hash"] = stable_text_feature(contract.strategy_version)
        features[f"{prefix}__family_hash"] = stable_text_feature(contract.family)
        features[f"{prefix}__reason_code_hash"] = stable_text_feature("|".join(contract.reason_codes))
    return features


def market_forecast_algorithm_signal_contracts(
    candles: list[dict[str, Any]],
    *,
    atr_value: float,
    regime_profile: dict[str, Any],
    rsi_value: float,
    adx_value: float,
) -> list[MarketForecastAlgorithmSignalContract]:
    setup_by_id = {
        "multi_timeframe_trend_alignment": market_forecast_trend_alignment_setup(candles, atr_value, adx_value),
        "first_pullback_after_open": market_forecast_first_pullback_setup(candles, atr_value),
        "failed_breakout_reversal": market_forecast_failed_breakout_reversal_setup(candles, atr_value),
        "liquidity_sweep_reversal": market_forecast_liquidity_sweep_reversal_setup(candles, atr_value),
        "bollinger_atr_reversion": market_forecast_bollinger_atr_reversion_setup(candles, rsi_value),
    }
    contracts: list[MarketForecastAlgorithmSignalContract] = []
    for entry in VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES:
        setup = setup_by_id.get(entry.strategyId, market_forecast_hold_setup(f"market_forecast.contract.{entry.strategyId}.not_implemented"))
        signal = str(setup["signal"])
        setup_quality = clamp(float(setup.get("confidence_or_setup_quality") or 0.0), 0.0, 1.0)
        family = strategy_family_value(entry.family)
        regime_compatibility = contract_regime_compatibility(family, signal, regime_profile, adx_value)
        eligible = bool(setup.get("eligibility")) and signal != "Hold" and regime_compatibility >= 0.35
        reason_codes = tuple(str(code) for code in setup.get("reason_codes", ()) if code)
        contracts.append(
            MarketForecastAlgorithmSignalContract(
                strategy_id=entry.strategyId,
                strategy_version=entry.strategyVersion,
                signal=signal,
                confidence_or_setup_quality=setup_quality,
                family=family,
                eligibility=eligible,
                reason_codes=reason_codes or (f"market_forecast.contract.{entry.strategyId}.evaluated",),
                regime_compatibility=regime_compatibility,
            )
        )
    return contracts


def market_forecast_hold_setup(reason_code: str) -> dict[str, Any]:
    return {
        "signal": "Hold",
        "confidence_or_setup_quality": 0.0,
        "eligibility": False,
        "reason_codes": (reason_code,),
    }


def strategy_family_value(family: Any) -> str:
    return str(getattr(family, "value", family)).upper()


def market_forecast_trend_alignment_setup(candles: list[dict[str, Any]], atr_value: float, adx_value: float) -> dict[str, Any]:
    if len(candles) < 50:
        return market_forecast_hold_setup("market_forecast.contract.multi_timeframe_trend_alignment.insufficient_history")
    closes = [float(candle["close"]) for candle in candles]
    ema9 = ema(closes, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    short_slope = slope(ema9[-6:]) / max(closes[-1], 0.01)
    medium_slope = slope(ema20[-10:]) / max(closes[-1], 0.01)
    trend_quality = clamp((abs(short_slope) * 900) + (abs(medium_slope) * 1200) + max(0.0, adx_value - 18) / 30, 0.0, 1.0)
    if ema9[-1] > ema20[-1] > ema50[-1] and short_slope > 0 and medium_slope > 0:
        return {
            "signal": "Buy",
            "confidence_or_setup_quality": max(0.35, trend_quality),
            "eligibility": trend_quality >= 0.35,
            "reason_codes": ("market_forecast.contract.multi_timeframe_trend_alignment.buy",),
        }
    if ema9[-1] < ema20[-1] < ema50[-1] and short_slope < 0 and medium_slope < 0:
        return {
            "signal": "Sell",
            "confidence_or_setup_quality": max(0.35, trend_quality),
            "eligibility": trend_quality >= 0.35,
            "reason_codes": ("market_forecast.contract.multi_timeframe_trend_alignment.sell",),
        }
    return {
        "signal": "Hold",
        "confidence_or_setup_quality": clamp(1 - abs(ema9[-1] - ema20[-1]) / max(atr_value, 0.01), 0.0, 0.55),
        "eligibility": False,
        "reason_codes": ("market_forecast.contract.multi_timeframe_trend_alignment.no_alignment",),
    }


def market_forecast_first_pullback_setup(candles: list[dict[str, Any]], atr_value: float) -> dict[str, Any]:
    if len(candles) < 12:
        return market_forecast_hold_setup("market_forecast.contract.first_pullback_after_open.insufficient_history")
    latest = candles[-1]
    prior = candles[-6:-1]
    impulse = float(prior[-1]["close"]) - float(prior[0]["open"])
    pullback = float(latest["close"]) - float(prior[-1]["close"])
    quality = clamp(abs(impulse) / max(atr_value * 1.5, 0.01), 0.0, 1.0)
    if impulse > atr_value and -atr_value <= pullback <= 0 and float(latest["close"]) > float(latest["open"]):
        return {
            "signal": "Buy",
            "confidence_or_setup_quality": max(0.35, quality),
            "eligibility": quality >= 0.35,
            "reason_codes": ("market_forecast.contract.first_pullback_after_open.buy",),
        }
    if impulse < -atr_value and 0 <= pullback <= atr_value and float(latest["close"]) < float(latest["open"]):
        return {
            "signal": "Sell",
            "confidence_or_setup_quality": max(0.35, quality),
            "eligibility": quality >= 0.35,
            "reason_codes": ("market_forecast.contract.first_pullback_after_open.sell",),
        }
    return {
        "signal": "Hold",
        "confidence_or_setup_quality": quality * 0.5,
        "eligibility": False,
        "reason_codes": ("market_forecast.contract.first_pullback_after_open.no_setup",),
    }


def market_forecast_failed_breakout_reversal_setup(candles: list[dict[str, Any]], atr_value: float) -> dict[str, Any]:
    if len(candles) < 22:
        return market_forecast_hold_setup("market_forecast.contract.failed_breakout_reversal.insufficient_history")
    latest = candles[-1]
    prior = candles[-22:-2]
    prior_high = max(float(candle["high"]) for candle in prior)
    prior_low = min(float(candle["low"]) for candle in prior)
    close = float(latest["close"])
    quality = clamp((float(latest["high"]) - float(latest["low"])) / max(atr_value, 0.01), 0.0, 1.0)
    if float(latest["high"]) > prior_high and close < prior_high:
        return {
            "signal": "Sell",
            "confidence_or_setup_quality": max(0.35, quality),
            "eligibility": quality >= 0.35,
            "reason_codes": ("market_forecast.contract.failed_breakout_reversal.sell",),
        }
    if float(latest["low"]) < prior_low and close > prior_low:
        return {
            "signal": "Buy",
            "confidence_or_setup_quality": max(0.35, quality),
            "eligibility": quality >= 0.35,
            "reason_codes": ("market_forecast.contract.failed_breakout_reversal.buy",),
        }
    return {
        "signal": "Hold",
        "confidence_or_setup_quality": quality * 0.4,
        "eligibility": False,
        "reason_codes": ("market_forecast.contract.failed_breakout_reversal.no_failed_break",),
    }


def market_forecast_liquidity_sweep_reversal_setup(candles: list[dict[str, Any]], atr_value: float) -> dict[str, Any]:
    if len(candles) < 10:
        return market_forecast_hold_setup("market_forecast.contract.liquidity_sweep_reversal.insufficient_history")
    latest = candles[-1]
    prior = candles[-10:-1]
    prior_high = max(float(candle["high"]) for candle in prior)
    prior_low = min(float(candle["low"]) for candle in prior)
    close = float(latest["close"])
    upper_rejection = float(latest["high"]) - max(float(latest["open"]), close)
    lower_rejection = min(float(latest["open"]), close) - float(latest["low"])
    if float(latest["high"]) > prior_high and upper_rejection >= atr_value * 0.35:
        return {
            "signal": "Sell",
            "confidence_or_setup_quality": clamp(upper_rejection / max(atr_value, 0.01), 0.35, 1.0),
            "eligibility": True,
            "reason_codes": ("market_forecast.contract.liquidity_sweep_reversal.sell",),
        }
    if float(latest["low"]) < prior_low and lower_rejection >= atr_value * 0.35:
        return {
            "signal": "Buy",
            "confidence_or_setup_quality": clamp(lower_rejection / max(atr_value, 0.01), 0.35, 1.0),
            "eligibility": True,
            "reason_codes": ("market_forecast.contract.liquidity_sweep_reversal.buy",),
        }
    return {
        "signal": "Hold",
        "confidence_or_setup_quality": clamp(max(upper_rejection, lower_rejection) / max(atr_value, 0.01), 0.0, 0.5),
        "eligibility": False,
        "reason_codes": ("market_forecast.contract.liquidity_sweep_reversal.no_sweep",),
    }


def market_forecast_bollinger_atr_reversion_setup(candles: list[dict[str, Any]], rsi_value: float) -> dict[str, Any]:
    closes = [float(candle["close"]) for candle in candles]
    if len(closes) < 20:
        return market_forecast_hold_setup("market_forecast.contract.bollinger_atr_reversion.insufficient_history")
    band_position = bollinger_band_position(closes, 20, 2)
    if band_position <= 0.05 or rsi_value <= 30:
        return {
            "signal": "Buy",
            "confidence_or_setup_quality": clamp(max(0.05 - band_position, 30 - rsi_value) / 30, 0.35, 1.0),
            "eligibility": True,
            "reason_codes": ("market_forecast.contract.bollinger_atr_reversion.buy",),
        }
    if band_position >= 0.95 or rsi_value >= 70:
        return {
            "signal": "Sell",
            "confidence_or_setup_quality": clamp(max(band_position - 0.95, rsi_value - 70) / 30, 0.35, 1.0),
            "eligibility": True,
            "reason_codes": ("market_forecast.contract.bollinger_atr_reversion.sell",),
        }
    return {
        "signal": "Hold",
        "confidence_or_setup_quality": clamp(abs(band_position - 0.5), 0.0, 0.5),
        "eligibility": False,
        "reason_codes": ("market_forecast.contract.bollinger_atr_reversion.no_reversion_extreme",),
    }


def contract_regime_compatibility(family: str, signal: str, regime_profile: dict[str, Any], adx_value: float) -> float:
    if signal == "Hold":
        return 0.0
    trend = str(regime_profile.get("trend") or "")
    normalized_family = family.upper()
    if normalized_family == "TREND":
        return 0.85 if trend in {"strong_uptrend", "strong_downtrend", "weak_uptrend", "weak_downtrend"} else 0.45
    if normalized_family in {"REVERSAL", "MEAN_REVERSION"}:
        return 0.85 if adx_value < 28 else 0.45
    if normalized_family == "BREAKOUT":
        return 0.75 if adx_value >= 18 else 0.45
    return 0.5


def algorithm_contract_family_scores(
    contracts: list[MarketForecastAlgorithmSignalContract],
    *,
    regime_profile: dict[str, Any],
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
    family_map = {
        "TREND": "trend",
        "BREAKOUT": "breakout",
        "MEAN_REVERSION": "mean_reversion",
        "REVERSAL": "reversal",
    }
    signed: list[float] = []
    for contract in contracts:
        prefix = family_map.get(contract.family.upper())
        if not prefix:
            continue
        contribution = contract.confidence_or_setup_quality * contract.regime_compatibility
        if contract.signal == "Buy":
            scores[f"{prefix}_buy_score"] += contribution
            signed.append(contribution)
        elif contract.signal == "Sell":
            scores[f"{prefix}_sell_score"] += contribution
            signed.append(-contribution)
    scores["confirmation_score"] = clamp(sum(signed) / len(signed), -1.0, 1.0) if signed else 0.0
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


def algorithm_contract_scores(contracts: list[MarketForecastAlgorithmSignalContract]) -> dict[str, float]:
    scores = {"buy": 0.0, "sell": 0.0, "hold": 0.0}
    if not contracts:
        return {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    for contract in contracts:
        contribution = contract.confidence_or_setup_quality * contract.regime_compatibility
        if contract.signal == "Buy" and contract.eligibility:
            scores["buy"] += contribution
            scores["hold"] += 1 - contribution
        elif contract.signal == "Sell" and contract.eligibility:
            scores["sell"] += contribution
            scores["hold"] += 1 - contribution
        else:
            scores["hold"] += max(0.5, 1 - contribution)
    total = sum(scores.values()) or 1
    return {key: value / total for key, value in scores.items()}


def stable_text_feature(value: str) -> float:
    import hashlib

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return int(digest, 16) / float(0xFFFFFFFFFFFF)


def signal_value(signal: str) -> float:
    if signal == "Buy":
        return 1.0
    if signal == "Sell":
        return -1.0
    return 0.0


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


def expected_success_move(features: dict[str, Any], latest_close: float, *, horizon_minutes: int = FORECAST_HORIZON_MINUTES) -> float:
    atr_value = features["volatility"]["atr_1m"]
    realized = features["volatility"]["realized_volatility"] * latest_close * math.sqrt(horizon_minutes)
    return max(0.05, atr_value, realized)


def volatility_adjusted_barriers(
    features: dict[str, Any],
    latest_close: float,
    *,
    artifact: dict[str, Any] | None = None,
    horizon_minutes: int = FORECAST_HORIZON_MINUTES,
) -> dict[str, float]:
    label_config = (artifact or {}).get("label") or {}
    configured_fixed_target = numeric(label_config.get("profitTargetDollars"))
    fixed_target = max(configured_fixed_target, DEFAULT_PROFIT_TARGET_DOLLARS)
    fixed_stop = numeric(label_config.get("stopLossDollars"))
    min_target_pct = numeric(label_config.get("minTargetPct")) if configured_fixed_target >= DEFAULT_PROFIT_TARGET_DOLLARS else DEFAULT_MIN_TARGET_PCT
    min_stop_pct = numeric(label_config.get("minStopPct")) or DEFAULT_MIN_STOP_PCT
    target_multiplier = numeric(label_config.get("targetAtrMultiplier")) or DEFAULT_TARGET_ATR_MULTIPLIER
    stop_multiplier = numeric(label_config.get("stopAtrMultiplier")) or DEFAULT_STOP_ATR_MULTIPLIER
    atr_value = float(features["volatility"]["atr_1m"])
    realized = float(features["volatility"]["realized_volatility"]) * latest_close * math.sqrt(horizon_minutes)
    atr_horizon = max(atr_value * math.sqrt(horizon_minutes), realized, 0.01)
    return {
        "targetDistance": max(fixed_target, latest_close * min_target_pct, atr_horizon * target_multiplier),
        "stopDistance": max(fixed_stop, latest_close * min_stop_pct, atr_horizon * stop_multiplier),
        "minTargetPct": min_target_pct,
        "minStopPct": min_stop_pct,
        "targetAtrMultiplier": target_multiplier,
        "stopAtrMultiplier": stop_multiplier,
        "fixedTargetDollars": fixed_target,
        "fixedStopDollars": fixed_stop,
        "atr5m": atr_horizon,
        "atrHorizon": atr_horizon,
        "horizonMinutes": horizon_minutes,
    }


def estimate_execution_quality(
    features: dict[str, Any],
    latest: dict[str, Any],
    barriers: dict[str, Any],
    *,
    spread: float | None,
    slippage: float,
    fees: float,
    latency_seconds: float = DEFAULT_DECISION_TO_SUBMISSION_LATENCY_SECONDS,
    horizon_minutes: int = FORECAST_HORIZON_MINUTES,
) -> dict[str, Any]:
    micro = features.get("microstructure") or {}
    volatility = features.get("volatility") or {}
    volume = features.get("volume") or {}
    atr_1m = max(float(volatility.get("atr_1m") or 0.0), 0.01)
    latest_close = max(float(latest.get("close") or 0.0), 0.01)
    observed_spread = max(0.0, float(spread if spread is not None else micro.get("avg_spread") or 0.0))
    base_cost = round(observed_spread + (max(0.0, float(slippage)) * 2) + max(0.0, float(fees)), 6)
    spread_atr = observed_spread / atr_1m
    max_spread_atr = max(float(micro.get("max_spread_atr") or 0.0), spread_atr)
    range_expansion = max(0.0, float(volatility.get("range_expansion") or 1.0))
    relative_volume = max(0.0, float(volume.get("relative_volume") or volume.get("volume_vs_20_bar_average") or 1.0))
    realized_volatility = max(0.0, float(volatility.get("realized_volatility") or 0.0))
    quote_imbalance = abs(float(micro.get("quote_imbalance") or 0.0))
    latency_minutes = max(0.0, latency_seconds) / 60.0
    quote_movement = atr_1m * math.sqrt(max(latency_minutes, 1 / 600))
    target_distance = max(float(barriers.get("targetDistance") or DEFAULT_PROFIT_TARGET_DOLLARS), 0.01)
    stop_distance = max(float(barriers.get("stopDistance") or DEFAULT_PROFIT_TARGET_DOLLARS), 0.01)
    volatility_penalty = clamp((range_expansion - 1.0) * 0.12 + realized_volatility * 180 + max_spread_atr * 0.18, 0.0, 0.45)
    liquidity_credit = clamp((relative_volume - 1.0) * 0.08, 0.0, 0.18)
    fill_probability = clamp(
        0.88
        - (spread_atr * 0.45)
        - (quote_movement / target_distance * 0.25)
        - volatility_penalty
        + liquidity_credit,
        0.15,
        0.98,
    )
    limit_order_non_fill_probability = 1.0 - fill_probability
    partial_fill_probability = clamp(0.06 + spread_atr * 0.22 + max(0.0, 1.0 - relative_volume) * 0.10 + volatility_penalty * 0.25, 0.02, 0.45)
    expected_partial_fill_fraction = clamp(1.0 - (partial_fill_probability * 0.45), 0.55, 1.0)
    stop_limit_miss_probability = clamp(0.02 + max_spread_atr * 0.20 + volatility_penalty * 0.35 + quote_imbalance * 0.04, 0.01, 0.45)
    opportunity_decay = clamp((latency_minutes / max(horizon_minutes, 1)) * (1 + range_expansion * 0.75) + limit_order_non_fill_probability * 0.12, 0.0, 0.6)
    adverse_selection_cost = max(0.0, observed_spread * 0.5 + quote_movement * 0.35 + atr_1m * volatility_penalty * 0.15)
    quote_movement_cost = quote_movement * 0.5
    cancel_replace_cost = observed_spread * limit_order_non_fill_probability * 0.35
    expected_stop_limit_miss_cost = stop_distance * stop_limit_miss_probability * 0.5
    realized_cost_error_reserve = max(observed_spread * 0.25, quote_movement * 0.25, base_cost * 0.10)
    total_estimated_cost = (
        base_cost
        + adverse_selection_cost
        + quote_movement_cost
        + cancel_replace_cost
        + expected_stop_limit_miss_cost
        + realized_cost_error_reserve
    )
    expected_execution_multiplier = fill_probability * expected_partial_fill_fraction * (1.0 - opportunity_decay)
    return {
        "metric": "incremental_realized_net_value_after_execution_costs",
        "baseCostFormula": "spread + 2*slippage + fees",
        "baseCost": round(base_cost, 6),
        "totalEstimatedCost": round(total_estimated_cost, 6),
        "fillProbability": round(fill_probability, 4),
        "limitOrderNonFillProbability": round(limit_order_non_fill_probability, 4),
        "partialFillProbability": round(partial_fill_probability, 4),
        "expectedPartialFillFraction": round(expected_partial_fill_fraction, 4),
        "stopLimitMissProbability": round(stop_limit_miss_probability, 4),
        "decisionToSubmissionLatencySeconds": round(max(0.0, latency_seconds), 4),
        "quoteMovementDuringLatency": round(quote_movement, 6),
        "opportunityDecay": round(opportunity_decay, 4),
        "adverseSelectionCost": round(adverse_selection_cost, 6),
        "cancelReplaceCost": round(cancel_replace_cost, 6),
        "expectedStopLimitMissCost": round(expected_stop_limit_miss_cost, 6),
        "realizedVsEstimatedCostErrorReserve": round(realized_cost_error_reserve, 6),
        "expectedExecutionMultiplier": round(expected_execution_multiplier, 4),
        "spreadAtr": round(spread_atr, 4),
        "maxSpreadAtr": round(max_spread_atr, 4),
        "inputs": {
            "spread": round(observed_spread, 6),
            "slippagePerShare": round(max(0.0, float(slippage)), 6),
            "fees": round(max(0.0, float(fees)), 6),
            "atr1m": round(atr_1m, 6),
            "latestClose": round(latest_close, 6),
            "horizonMinutes": horizon_minutes,
        },
    }


def forecast_future_price_prediction(
    features: dict[str, Any],
    latest_close: float,
    *,
    probabilities: dict[str, float],
    barriers: dict[str, float],
    market_regime: dict[str, Any],
    horizon_minutes: int = FORECAST_HORIZON_MINUTES,
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
        "horizonMinutes": horizon_minutes,
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
        "basis": f"{horizon_minutes}-minute expected close from probabilities, trend indicators, strategy scores, regime, volatility, and session.",
    }


def no_edge_future_price_prediction(
    latest_close: float,
    reason: str,
    *,
    horizon_minutes: int = FORECAST_HORIZON_MINUTES,
) -> dict[str, Any]:
    latest_close = max(numeric(latest_close), 0.01)
    return {
        "horizonMinutes": horizon_minutes,
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
        "basis": f"{horizon_minutes}-minute neutral expected close. {reason}.",
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
    execution_quality: dict[str, Any] | None = None,
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
    execution_quality = execution_quality or {}
    fill_probability = float(execution_quality.get("fillProbability") if execution_quality.get("fillProbability") is not None else 1.0)
    non_fill_probability = float(execution_quality.get("limitOrderNonFillProbability") if execution_quality.get("limitOrderNonFillProbability") is not None else 0.0)
    stop_limit_miss_probability = float(execution_quality.get("stopLimitMissProbability") if execution_quality.get("stopLimitMissProbability") is not None else 0.0)
    opportunity_decay = float(execution_quality.get("opportunityDecay") if execution_quality.get("opportunityDecay") is not None else 0.0)
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
        reasons.append(f"Execution-adjusted net expected value {expected_value:+.2f}/share is not positive")
    if spread_atr > DEFAULT_MAX_SPREAD_ATR:
        reasons.append(f"Spread/ATR {spread_atr:.1%} is above {DEFAULT_MAX_SPREAD_ATR:.0%}")
    if fill_probability < MIN_EXECUTION_FILL_PROBABILITY:
        reasons.append(f"Fill probability {fill_probability:.1%} is below {MIN_EXECUTION_FILL_PROBABILITY:.0%}")
    if non_fill_probability > MAX_LIMIT_ORDER_NON_FILL_PROBABILITY:
        reasons.append(f"Limit-order non-fill probability {non_fill_probability:.1%} is above {MAX_LIMIT_ORDER_NON_FILL_PROBABILITY:.0%}")
    if stop_limit_miss_probability > MAX_STOP_LIMIT_MISS_PROBABILITY:
        reasons.append(f"Stop-limit miss probability {stop_limit_miss_probability:.1%} is above {MAX_STOP_LIMIT_MISS_PROBABILITY:.0%}")
    if opportunity_decay > MAX_OPPORTUNITY_DECAY:
        reasons.append(f"Opportunity decay {opportunity_decay:.1%} is above {MAX_OPPORTUNITY_DECAY:.0%}")
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
        "fillProbability": round(fill_probability, 4),
        "minimumFillProbability": MIN_EXECUTION_FILL_PROBABILITY,
        "limitOrderNonFillProbability": round(non_fill_probability, 4),
        "maximumLimitOrderNonFillProbability": MAX_LIMIT_ORDER_NON_FILL_PROBABILITY,
        "stopLimitMissProbability": round(stop_limit_miss_probability, 4),
        "maximumStopLimitMissProbability": MAX_STOP_LIMIT_MISS_PROBABILITY,
        "opportunityDecay": round(opportunity_decay, 4),
        "maximumOpportunityDecay": MAX_OPPORTUNITY_DECAY,
        "expectedValue": expected_value if action != DECISION_NO_TRADE else max(buy_expected_value, sell_expected_value),
        "expectedValueMetric": "incremental_realized_net_value_after_execution_costs",
        "positionSizeMultiplier": float(market_regime.get("positionSizeMultiplier") or 0.0) if action != DECISION_NO_TRADE else 0.0,
        "reasons": reasons or [f"{candidate_action.title()} edge passed confidence, gap, execution-adjusted expectancy, fill, and regime gates"],
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
        f"Incremental net EV {float(decision['expectedValue']):+.2f}/share after execution costs",
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
    contracts = []
    for entry in VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES:
        prefix = f"strategy__{entry.strategyId}"
        contracts.append(
            {
                "strategyId": entry.strategyId,
                "strategyVersion": entry.strategyVersion,
                "signal": signal_label(float(algorithm.get(f"{prefix}__signal") or 0)),
                "confidenceOrSetupQuality": algorithm.get(f"{prefix}__confidence_or_setup_quality"),
                "family": strategy_family_value(entry.family),
                "eligibility": bool(algorithm.get(f"{prefix}__eligible")),
                "regimeCompatibility": algorithm.get(f"{prefix}__regime_compatibility"),
                "reasonCodeHash": algorithm.get(f"{prefix}__reason_code_hash"),
            }
        )
    return {
        "contractSchemaVersion": algorithm.get("contract_schema_version"),
        "contracts": contracts,
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
    return FORECAST_ACTIVE_ARTIFACT_DIR / f"{symbol.upper()}.json"


def legacy_market_forecast_artifact_path(symbol: str) -> Path:
    return MODEL_ARTIFACT_DIR / f"{MODEL_VERSION}_{symbol.upper()}.json"


def market_forecast_candidate_artifact_path(artifact_id: str) -> Path:
    return FORECAST_CANDIDATE_ARTIFACT_DIR / f"{safe_artifact_id(artifact_id)}.json"


def market_forecast_candidate_model_path(artifact_id: str) -> Path:
    return FORECAST_CANDIDATE_ARTIFACT_DIR / f"{safe_artifact_id(artifact_id)}.xgboost.json"


def market_forecast_fold_model_path(artifact_id: str, fold: int) -> Path:
    return FORECAST_CANDIDATE_ARTIFACT_DIR / f"{safe_artifact_id(artifact_id)}.fold{int(fold)}.xgboost.json"


def safe_artifact_id(value: str) -> str:
    normalized = "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in str(value))
    return normalized.strip("._") or "market_forecast_candidate"


def load_market_forecast_artifact(symbol: str) -> dict[str, Any] | None:
    path = market_forecast_artifact_path(symbol)
    if not path.exists():
        return None
    return load_market_forecast_artifact_file(path)


def load_market_forecast_artifact_file(path: Path) -> dict[str, Any] | None:
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


def load_market_forecast_candidate_artifact(artifact_id: str) -> dict[str, Any] | None:
    return load_market_forecast_artifact_file(market_forecast_candidate_artifact_path(artifact_id))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def promote_market_forecast_candidate(
    artifact_id: str,
    *,
    symbol: str,
    target_state: str = MODEL_STATE_ACTIVE,
    promoted_by: str = "manual",
    reason: str = "",
) -> dict[str, Any]:
    if target_state != MODEL_STATE_ACTIVE:
        raise ValueError("Only ACTIVE promotion updates artifacts/active/<SYMBOL>.json")
    candidate = load_market_forecast_candidate_artifact(artifact_id)
    if not candidate:
        raise FileNotFoundError(f"Candidate market forecast artifact not found: {artifact_id}")
    if str(candidate.get("lifecycleState") or "") not in {
        MODEL_STATE_TRAINED_CANDIDATE,
        MODEL_STATE_VALIDATED,
        MODEL_STATE_SHADOW,
        MODEL_STATE_PAPER_APPROVED,
    }:
        raise ValueError(f"Candidate state cannot be promoted: {candidate.get('lifecycleState')}")
    gate_report = market_forecast_promotion_validation_gates(candidate)
    if gate_report["passed"] is not True:
        failed = ", ".join(gate["id"] for gate in gate_report["gates"] if not gate["passed"])
        raise ValueError(f"Market forecast candidate cannot become ACTIVE until validation gates pass: {failed}")
    oos_proof = gate_report["outOfSampleExecutionValueProof"]
    normalized_symbol = symbol.upper()
    active_path = market_forecast_artifact_path(normalized_symbol)
    promoted_at = datetime.now(UTC).isoformat()
    rollback_path: Path | None = None
    if active_path.exists():
        previous = load_market_forecast_artifact_file(active_path)
        previous_id = safe_artifact_id(str((previous or {}).get("artifactId") or "previous_active"))
        rollback_path = FORECAST_ACTIVE_HISTORY_DIR / normalized_symbol / f"{promoted_at.replace(':', '').replace('+', '_')}_{previous_id}.json"
        rollback_payload = {
            **(previous or {}),
            "lifecycleState": MODEL_STATE_RETIRED,
            "retiredAt": promoted_at,
            "retiredByPromotionArtifactId": artifact_id,
        }
        write_json_atomic(rollback_path, rollback_payload)
    active_payload = {
        **candidate,
        "lifecycleState": MODEL_STATE_ACTIVE,
        "promotionStatus": MODEL_STATE_ACTIVE,
        "approved": True,
        "activeSymbol": normalized_symbol,
        "promotedAt": promoted_at,
        "promotedBy": promoted_by,
        "promotionReason": reason,
        "outOfSampleExecutionValueProof": oos_proof,
        "promotionValidationGates": gate_report,
        "sourceCandidatePath": str(market_forecast_candidate_artifact_path(artifact_id)),
        "rollbackArtifactPath": str(rollback_path) if rollback_path else None,
    }
    write_json_atomic(active_path, active_payload)
    return {
        "status": "promoted",
        "artifactId": artifact_id,
        "symbol": normalized_symbol,
        "activePath": str(active_path),
        "rollbackArtifactPath": str(rollback_path) if rollback_path else None,
        "reversible": rollback_path is not None,
        "outOfSampleExecutionValueProof": oos_proof,
        "promotionValidationGates": gate_report,
    }


def market_forecast_promotion_validation_gates(candidate: dict[str, Any]) -> dict[str, Any]:
    oos_proof = market_forecast_out_of_sample_execution_value_proof(candidate)
    lifecycle_state = str(candidate.get("lifecycleState") or "")
    metrics = candidate.get("metrics") or {}
    final_metrics = metrics.get("finalUntouchedTest") or candidate.get("finalUntouchedTest") or {}
    threshold_source = str(((candidate.get("optimizationPolicy") or {}).get("thresholdSource") or ""))
    validation_parity = candidate.get("validationDeploymentParity") or {}
    walk_forward = candidate.get("walkForwardValidation") or {}
    calibration = candidate.get("calibration") or {}
    feature_names = candidate.get("featureNames") or []
    gates = [
        promotion_validation_gate(
            "market_forecast.lifecycle.promotable_state",
            lifecycle_state in {MODEL_STATE_TRAINED_CANDIDATE, MODEL_STATE_VALIDATED, MODEL_STATE_SHADOW, MODEL_STATE_PAPER_APPROVED},
            f"Candidate lifecycle state is {lifecycle_state}.",
        ),
        promotion_validation_gate(
            "market_forecast.oos.positive_incremental_net_value",
            oos_proof.get("passed") is True,
            f"OOS proof status is {oos_proof.get('status')}; metric {oos_proof.get('metric')}.",
        ),
        promotion_validation_gate(
            "market_forecast.oos.final_holdout_rows_present",
            int(final_metrics.get("rows") or candidate.get("finalUntouchedTestRows") or 0) > 0,
            "Final untouched test rows must be present.",
        ),
        promotion_validation_gate(
            "market_forecast.threshold.not_selected_from_final_holdout",
            threshold_source != "final_untouched_test",
            f"Threshold source is {threshold_source or 'unspecified'}.",
        ),
        promotion_validation_gate(
            "market_forecast.validation.walk_forward_present",
            bool(walk_forward.get("summary") or metrics.get("walkForward")),
            "Walk-forward validation summary must be present.",
        ),
        promotion_validation_gate(
            "market_forecast.validation.deployment_parity_present",
            bool(validation_parity or candidate.get("trainingModelKind")),
            "Validation/deployment parity metadata must be present.",
        ),
        promotion_validation_gate(
            "market_forecast.calibration.present",
            bool(calibration),
            "Probability calibration metadata must be present.",
        ),
        promotion_validation_gate(
            "market_forecast.features.schema_present",
            bool(feature_names or candidate.get("featureSchemaHash") or validation_parity.get("featureSchemaHash")),
            "Feature schema identity must be present.",
        ),
    ]
    passed = all(gate["passed"] for gate in gates)
    return {
        "passed": passed,
        "gateSet": "market_forecast_active_promotion_v1",
        "checkedAt": utc_now_iso(),
        "gates": gates,
        "failedGateIds": [gate["id"] for gate in gates if not gate["passed"]],
        "outOfSampleExecutionValueProof": oos_proof,
        "policy": "manual_promotion_is_allowed_only_after_all_validation_gates_pass",
    }


def promotion_validation_gate(gate_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": gate_id, "passed": bool(passed), "detail": detail}


def market_forecast_out_of_sample_execution_value_proof(artifact: dict[str, Any]) -> dict[str, Any]:
    explicit = artifact.get("outOfSampleExecutionValueProof")
    if isinstance(explicit, dict) and explicit.get("metric") == "incremental_realized_net_value_after_execution_costs":
        return explicit
    final_metrics = ((artifact.get("metrics") or {}).get("finalUntouchedTest") or artifact.get("finalUntouchedTest") or {})
    expected_value = final_metrics.get("expectedValue") or {}
    selected_trades = int(expected_value.get("selectedTrades") or 0)
    average_selected = numeric(expected_value.get("averageEvSelected"))
    total_selected = numeric(expected_value.get("totalEvSelected"))
    rows = int(final_metrics.get("rows") or artifact.get("finalUntouchedTestRows") or 0)
    passed = (
        rows > 0
        and selected_trades >= MIN_MARKET_FORECAST_OOS_SELECTED_TRADES
        and average_selected > 0
        and total_selected > 0
    )
    reason_codes = []
    if rows <= 0:
        reason_codes.append("market_forecast.oos.no_final_untouched_test_rows")
    if selected_trades < MIN_MARKET_FORECAST_OOS_SELECTED_TRADES:
        reason_codes.append("market_forecast.oos.no_selected_final_holdout_trades")
    if average_selected <= 0:
        reason_codes.append("market_forecast.oos.average_net_value_not_positive")
    if total_selected <= 0:
        reason_codes.append("market_forecast.oos.total_net_value_not_positive")
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "metric": "incremental_realized_net_value_after_execution_costs",
        "method": "final_untouched_test_report_only",
        "rows": rows,
        "selectedTrades": selected_trades,
        "averageNetValueSelectedRows": round(average_selected, 6),
        "totalNetValueSelectedRows": round(total_selected, 6),
        "source": "metrics.finalUntouchedTest.expectedValue",
        "reasonCodes": tuple(reason_codes or ["market_forecast.oos.positive_after_execution_costs"]),
    }


def rollback_active_market_forecast_artifact(
    *,
    symbol: str,
    rollback_artifact_path: str,
    rolled_back_by: str = "manual",
    reason: str = "",
) -> dict[str, Any]:
    normalized_symbol = symbol.upper()
    source = Path(rollback_artifact_path)
    previous = load_market_forecast_artifact_file(source)
    if not previous:
        raise FileNotFoundError(f"Rollback market forecast artifact not found: {rollback_artifact_path}")
    active_path = market_forecast_artifact_path(normalized_symbol)
    rolled_back_at = datetime.now(UTC).isoformat()
    rollback_payload = {
        **previous,
        "lifecycleState": MODEL_STATE_ACTIVE,
        "promotionStatus": MODEL_STATE_ACTIVE,
        "approved": True,
        "activeSymbol": normalized_symbol,
        "rolledBackAt": rolled_back_at,
        "rolledBackBy": rolled_back_by,
        "rollbackReason": reason,
        "rollbackSourcePath": str(source),
    }
    write_json_atomic(active_path, rollback_payload)
    return {
        "status": "rolled_back",
        "symbol": normalized_symbol,
        "activePath": str(active_path),
        "rollbackSourcePath": str(source),
    }


def is_approved_market_forecast_artifact(artifact: dict[str, Any] | None) -> bool:
    if not artifact:
        return False
    promotion_status = str(artifact.get("promotionStatus") or artifact.get("status") or "").lower()
    lifecycle_state = str(artifact.get("lifecycleState") or "").upper()
    return artifact.get("approved") is True and (lifecycle_state == MODEL_STATE_ACTIVE or promotion_status == MODEL_STATE_ACTIVE.lower())


def select_approved_forecast_artifact(symbol: str) -> ApprovedForecastArtifact | None:
    artifact = load_market_forecast_artifact(symbol)
    if not is_approved_market_forecast_artifact(artifact):
        return None
    return ApprovedForecastArtifact(symbol=symbol.upper(), payload=artifact)


def load_approved_market_forecast_artifact(symbol: str) -> dict[str, Any] | None:
    artifact = select_approved_forecast_artifact(symbol)
    return artifact.payload if artifact else None


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
    approved_artifact = artifact if is_approved_market_forecast_artifact(artifact) else None
    if approved_artifact:
        artifact = approved_artifact
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
    if artifact:
        return {
            "status": "model_unapproved",
            "kind": str(artifact.get("modelKind") or "trained-unapproved"),
            "version": MODEL_VERSION,
            "trainedAt": artifact.get("trainedAt"),
            "trainingRows": artifact.get("trainingRows"),
            "testRows": artifact.get("testRows"),
            "calibration": (artifact.get("calibration") or {}).get("method"),
            "uncertaintyMembers": 0,
            "libraryCandidates": libraries,
            "installedLibraries": installed,
            "message": "A trained market forecast artifact exists, but it is not explicitly approved for order authorization.",
        }
    return {
        "status": "model_unavailable",
        "kind": HEURISTIC_ESTIMATE_NOT_ML,
        "version": MODEL_VERSION,
        "calibration": None,
        "uncertaintyMembers": 0,
        "libraryCandidates": libraries,
        "installedLibraries": installed,
        "message": "No approved market forecast model is available; heuristic estimate is UI diagnostics only.",
    }


def missing_runtime_inputs(symbol: str = "SPY") -> list[str]:
    missing: list[str] = []
    artifact = load_market_forecast_artifact(symbol)
    if not artifact:
        missing.insert(0, "approved LightGBM/XGBoost/CatBoost model artifact")
    elif not is_approved_market_forecast_artifact(artifact):
        missing.insert(0, "explicit market forecast model approval")
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
