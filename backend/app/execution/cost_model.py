from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from backend.app.tick_data import quote_mid_and_spread


EXECUTION_COST_MODEL_VERSION = "execution_cost_model_v1"
EXECUTION_COST_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "execution_cost_model"
EXECUTION_COST_LEDGER_DIR = EXECUTION_COST_DATA_DIR / "ledger"
EXECUTION_COST_ARTIFACT_ROOT = EXECUTION_COST_DATA_DIR / "artifacts"
EXECUTION_COST_CANDIDATE_DIR = EXECUTION_COST_ARTIFACT_ROOT / "candidates"
EXECUTION_COST_ACTIVE_DIR = EXECUTION_COST_ARTIFACT_ROOT / "active"
EXECUTION_COST_ACTIVE_HISTORY_DIR = EXECUTION_COST_ARTIFACT_ROOT / "active_history"

MODEL_STATE_TRAINED_CANDIDATE = "TRAINED_CANDIDATE"
MODEL_STATE_VALIDATED = "VALIDATED"
MODEL_STATE_SHADOW = "SHADOW"
MODEL_STATE_PAPER_APPROVED = "PAPER_APPROVED"
MODEL_STATE_ACTIVE = "ACTIVE"
MODEL_STATE_RETIRED = "RETIRED"
MODEL_STATE_REJECTED = "REJECTED"
MIN_PAPER_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL = 200
MIN_PAPER_DAYS_FOR_ACTIVE_EXECUTION_COST_MODEL = 3
MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL = 50


@dataclass(frozen=True)
class ApprovedExecutionCostArtifact:
    symbol: str
    payload: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in value)[:160] or "artifact"


def symbol_key(symbol: str) -> str:
    return safe_id(symbol.upper())


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def active_artifact_path(symbol: str) -> Path:
    return EXECUTION_COST_ACTIVE_DIR / f"{symbol_key(symbol)}.json"


def candidate_artifact_path(artifact_id: str) -> Path:
    return EXECUTION_COST_CANDIDATE_DIR / f"{safe_id(artifact_id)}.json"


def ledger_path(symbol: str, day: str) -> Path:
    return EXECUTION_COST_LEDGER_DIR / f"{symbol_key(symbol)}_{day}.json"


def event_day(timestamp: str | None) -> str:
    return str(timestamp or utc_now_iso())[:10]


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_active_artifact(symbol: str) -> dict[str, Any] | None:
    artifact = load_json(active_artifact_path(symbol))
    if not artifact or artifact.get("version") != EXECUTION_COST_MODEL_VERSION:
        return None
    if artifact.get("approved") is not True or artifact.get("lifecycleState") != MODEL_STATE_ACTIVE:
        return None
    return artifact


def select_approved_execution_cost_artifact(symbol: str) -> ApprovedExecutionCostArtifact | None:
    artifact = load_active_artifact(symbol)
    return ApprovedExecutionCostArtifact(symbol=symbol.upper(), payload=artifact) if artifact else None


def record_execution_cost_observation(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_observation(payload)
    path = ledger_path(normalized["symbol"], event_day(normalized.get("decisionTimestamp") or normalized.get("orderSubmissionTimestamp")))
    document = load_json(path) or {
        "version": EXECUTION_COST_MODEL_VERSION,
        "ledgerName": "execution_cost_observation_ledger",
        "recordingPolicy": "paper_or_live_order_lifecycle_events_only",
        "symbol": normalized["symbol"],
        "date": event_day(normalized.get("decisionTimestamp") or normalized.get("orderSubmissionTimestamp")),
        "records": [],
    }
    records = list(document.get("records") or [])
    existing = next((index for index, item in enumerate(records) if item.get("observationId") == normalized["observationId"]), None)
    if existing is None:
        records.append(normalized)
    else:
        records[existing] = {**records[existing], **normalized}
    records.sort(key=lambda item: str(item.get("orderSubmissionTimestamp") or item.get("decisionTimestamp") or ""))
    document["records"] = records
    document["updatedAt"] = utc_now_iso()
    write_json_atomic(path, document)
    return {"saved": True, "path": str(path), "observationId": normalized["observationId"], "records": len(records)}


def record_execution_cost_observation_from_order_log(payload: dict[str, Any]) -> dict[str, Any]:
    return record_execution_cost_observation(execution_cost_payload_from_order_log(payload))


def record_execution_cost_observations_from_order_logs(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    results = [record_execution_cost_observation_from_order_log(payload) for payload in payloads]
    return {
        "saved": len(results),
        "observationIds": [result["observationId"] for result in results],
        "paths": sorted({result["path"] for result in results}),
    }


def execution_cost_payload_from_order_log(payload: dict[str, Any]) -> dict[str, Any]:
    intent = object_payload(payload.get("intent") or payload.get("orderIntent") or payload.get("order_intent"))
    result = object_payload(payload.get("result") or payload.get("gatewayResult") or payload.get("paperGatewayResult") or payload)
    fill = object_payload(payload.get("fill") or payload.get("fillUpdate") or result.get("fill") or result.get("fillUpdate"))
    ack = object_payload(payload.get("brokerAck") or result.get("brokerAck"))
    market = object_payload(payload.get("marketSnapshot") or payload.get("market") or payload.get("quoteSnapshot"))
    tick_data = object_payload(payload.get("tickData") or payload.get("quoteTradeTicks"))
    quote_ticks = list(payload.get("quoteTicks") or tick_data.get("quotes") or [])
    trade_ticks = list(payload.get("tradeTicks") or tick_data.get("trades") or [])
    execution_quality = object_payload(payload.get("executionQuality") or payload.get("forecastExecutionQuality"))
    reason_codes = tuple(str(code) for code in [
        *(payload.get("reasonCodes") or ()),
        *(intent.get("reasonCodes") or ()),
        *(result.get("reasonCodes") or ()),
    ])
    symbol = str(payload.get("symbol") or intent.get("symbol") or fill.get("symbol") or "SPY").upper()
    order_type = str(payload.get("orderType") or intent.get("orderType") or inferred_order_type(intent)).lower()
    submitted_quantity = numeric(payload.get("submittedQuantity") or intent.get("submittedQuantity") or result.get("submittedQuantity") or payload.get("quantity"))
    filled_quantity = numeric(payload.get("filledQuantity") or fill.get("filledQuantity") or result.get("filledQuantity"))
    decision_timestamp = first_present(payload.get("decisionTimestamp"), intent.get("decisionTimestamp"), payload.get("eventTimestamp"))
    order_submission_timestamp = first_present(payload.get("orderSubmissionTimestamp"), ack.get("acceptedAt"), result.get("evaluatedAt"), intent.get("createdAt"))
    decision_quote = quote_mid_and_spread(quote_ticks, decision_timestamp) if quote_ticks else {"mid": None, "spread": None}
    submit_quote = quote_mid_and_spread(quote_ticks, order_submission_timestamp) if quote_ticks else {"mid": None, "spread": None}
    nearest_fill_trade = nearest_trade_snapshot(trade_ticks, payload.get("fillTimestamp") or fill.get("filledAt") or fill.get("updatedAt"))
    return {
        "observationId": payload.get("observationId") or f"{symbol}|{result.get('clientOrderId') or intent.get('clientOrderId') or intent.get('orderIntentId') or utc_now_iso()}",
        "sourceRecordType": "order_fill_lifecycle_log",
        "symbol": symbol,
        "side": payload.get("side") or intent.get("side") or fill.get("side"),
        "orderType": order_type,
        "sourceMode": payload.get("sourceMode") or execution_source_mode(payload, intent, result),
        "status": payload.get("status") or result.get("status") or fill.get("status") or ack.get("status"),
        "submitted": payload.get("submitted") if "submitted" in payload else result.get("submitted"),
        "clientOrderId": payload.get("clientOrderId") or result.get("clientOrderId") or intent.get("clientOrderId") or fill.get("clientOrderId"),
        "orderIntentId": payload.get("orderIntentId") or result.get("orderIntentId") or intent.get("orderIntentId") or fill.get("orderIntentId"),
        "decisionTimestamp": decision_timestamp,
        "orderSubmissionTimestamp": order_submission_timestamp,
        "fillTimestamp": payload.get("fillTimestamp") or fill.get("filledAt") or fill.get("updatedAt"),
        "submittedQuantity": submitted_quantity,
        "filledQuantity": filled_quantity,
        "averageFillPrice": payload.get("averageFillPrice") or fill.get("averageFillPrice"),
        "limitPrice": payload.get("limitPrice") or intent.get("limitPrice"),
        "triggerPrice": payload.get("triggerPrice") or intent.get("triggerPrice"),
        "midAtDecision": payload.get("midAtDecision") or decision_quote.get("mid") or midpoint(payload.get("bidAtDecision"), payload.get("askAtDecision")) or midpoint(market.get("bid"), market.get("ask")) or market.get("midAtDecision"),
        "midAtSubmit": payload.get("midAtSubmit") or submit_quote.get("mid") or midpoint(payload.get("bidAtSubmit"), payload.get("askAtSubmit")) or market.get("midAtSubmit"),
        "spreadAtDecision": payload.get("spreadAtDecision") or decision_quote.get("spread") or spread(payload.get("bidAtDecision"), payload.get("askAtDecision")) or spread(market.get("bid"), market.get("ask")) or market.get("spread"),
        "fees": payload.get("fees") or execution_quality.get("fees"),
        "estimatedExecutionCost": payload.get("estimatedExecutionCost") or execution_quality.get("totalEstimatedCost"),
        "reasonCodes": reason_codes,
        "stopLimitMiss": payload.get("stopLimitMiss") if "stopLimitMiss" in payload else inferred_stop_limit_miss(order_type, result, fill, reason_codes),
        "tickExecutionQualityValidation": {
            "status": "available" if quote_ticks or trade_ticks else "unavailable",
            "quoteTickCount": len(quote_ticks),
            "tradeTickCount": len(trade_ticks),
            "decisionMid": decision_quote.get("mid"),
            "submissionMid": submit_quote.get("mid"),
            "decisionSpread": decision_quote.get("spread"),
            "submissionSpread": submit_quote.get("spread"),
            "quoteMovementDuringLatency": quote_movement_between_midpoints(decision_quote.get("mid"), submit_quote.get("mid"), normalize_side(payload.get("side") or intent.get("side") or fill.get("side"))),
            "nearestFillTradeTimestamp": nearest_fill_trade.get("timestamp"),
            "nearestFillTradePrice": nearest_fill_trade.get("price"),
        },
        "rawLifecycleLog": payload,
    }


def normalize_observation(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or "SPY").upper()
    order_id = str(payload.get("clientOrderId") or payload.get("orderIntentId") or payload.get("forecastInvocationId") or utc_now_iso())
    submitted_qty = max(0.0, numeric(payload.get("submittedQuantity") or payload.get("submittedQty") or payload.get("quantity")))
    filled_qty = max(0.0, numeric(payload.get("filledQuantity") or payload.get("filledQty")))
    average_fill_price = optional_numeric(payload.get("averageFillPrice") or payload.get("avgFillPrice"))
    decision_mid = optional_numeric(payload.get("midAtDecision"))
    submit_mid = optional_numeric(payload.get("midAtSubmit"))
    side = normalize_side(payload.get("side"))
    status = str(payload.get("status") or "").upper()
    submitted = bool(payload.get("submitted")) or status in {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED"}
    fill_fraction = filled_qty / submitted_qty if submitted_qty > 0 else 0.0
    latency_ms = latency_milliseconds(payload)
    realized_cost = realized_execution_cost(payload, side=side, average_fill_price=average_fill_price, decision_mid=decision_mid)
    estimated_cost = numeric(payload.get("estimatedExecutionCost") or payload.get("expectedExecutionCost"))
    adverse_selection = adverse_selection_cost(payload, side=side, average_fill_price=average_fill_price, decision_mid=decision_mid)
    labels = {
        "filled": filled_qty > 0,
        "fullFill": submitted_qty > 0 and filled_qty >= submitted_qty,
        "partialFill": 0 < filled_qty < submitted_qty,
        "partialFillFraction": round(fill_fraction, 6),
        "nonFill": submitted and submitted_qty > 0 and filled_qty <= 0,
        "stopLimitMiss": bool(payload.get("stopLimitMiss")),
        "adverseSelectionCost": round(adverse_selection, 6) if adverse_selection is not None else None,
        "latencyCost": round(latency_cost(side, decision_mid, submit_mid), 6),
        "realizedExecutionCost": round(realized_cost, 6),
        "realizedCostError": round(realized_cost - estimated_cost, 6),
        "incrementalRealizedNetValue": round(numeric(payload.get("incrementalRealizedNetValue") or payload.get("incrementalRealizedNetValueAfterExecutionCosts")), 6),
    }
    return {
        **json_compatible(payload),
        "observationId": safe_id(str(payload.get("observationId") or f"{symbol}|{order_id}")),
        "symbol": symbol,
        "side": side,
        "orderType": str(payload.get("orderType") or "limit").lower(),
        "sourceMode": str(payload.get("sourceMode") or payload.get("mode") or "unknown").lower(),
        "status": status or None,
        "submitted": submitted,
        "submittedQuantity": submitted_qty,
        "filledQuantity": filled_qty,
        "averageFillPrice": average_fill_price,
        "midAtDecision": decision_mid,
        "midAtSubmit": submit_mid,
        "decisionToSubmissionLatencyMs": latency_ms,
        "calibrationInputs": {
            "fillLifecycleReady": submitted_qty > 0 and (submitted or status in {"REJECTED", "NOT_SUBMITTED"}),
            "fillPriceReady": average_fill_price is not None,
            "decisionMidReady": decision_mid is not None,
            "submitMidReady": submit_mid is not None,
            "adverseSelectionReady": adverse_selection is not None,
            "stopLimitMissReady": str(payload.get("orderType") or "").lower() == "stop_limit" or "stopLimitMiss" in payload,
            "quoteTradeTickValidationReady": (payload.get("tickExecutionQualityValidation") or {}).get("status") == "available",
        },
        "labels": labels,
        "recordedAt": utc_now_iso(),
    }


def train_execution_cost_candidate(symbol: str, *, min_rows: int = 20) -> dict[str, Any]:
    rows = load_observations(symbol)
    usable = [row for row in rows if is_training_observation(row)]
    if len(usable) < min_rows:
        return {
            "status": "insufficient_data",
            "symbol": symbol.upper(),
            "usableRows": len(usable),
            "minimumRows": min_rows,
            "promotionRequired": False,
            "reasonCodes": ("execution_cost.training.insufficient_observations",),
        }
    trained_at = utc_now_iso()
    artifact_id = safe_id(f"execution_cost_{symbol.upper()}_{trained_at}_{len(usable)}")
    source_modes = sorted({str(row.get("sourceMode") or "unknown") for row in usable})
    session_days = sorted({event_day(str(row.get("orderSubmissionTimestamp") or row.get("decisionTimestamp") or "")) for row in usable})
    partitions = chronological_execution_cost_partitions(usable)
    model_training_rows = usable
    model = empirical_model(model_training_rows)
    out_of_sample_proof = out_of_sample_execution_value_proof(partitions["training"], partitions["finalUntouchedTest"])
    artifact = {
        "version": EXECUTION_COST_MODEL_VERSION,
        "artifactId": artifact_id,
        "symbol": symbol.upper(),
        "modelKind": "empirical_execution_cost_model",
        "lifecycleState": MODEL_STATE_TRAINED_CANDIDATE,
        "promotionStatus": MODEL_STATE_TRAINED_CANDIDATE,
        "approved": False,
        "trainedAt": trained_at,
        "trainingRows": len(usable),
        "modelTrainingRows": len(model_training_rows),
        "sourceModes": source_modes,
        "paperTradingValidated": paper_validation_passed(usable, source_modes, session_days),
        "paperDays": len([day for day in session_days if day]),
        "minimumPaperRowsForActive": MIN_PAPER_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL,
        "minimumPaperDaysForActive": MIN_PAPER_DAYS_FOR_ACTIVE_EXECUTION_COST_MODEL,
        "minimumOutOfSampleRowsForActive": MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL,
        "featureSchemaHash": execution_feature_schema_hash(),
        "partitions": {
            "training": partition_summary(partitions["training"]),
            "finalUntouchedTest": partition_summary(partitions["finalUntouchedTest"]),
            "policy": "chronological_train_then_final_untouched_holdout_report_only",
        },
        "outOfSampleExecutionValueProof": out_of_sample_proof,
        "labels": (
            "filled",
            "fullFill",
            "partialFill",
            "partialFillFraction",
            "nonFill",
            "stopLimitMiss",
            "adverseSelectionCost",
            "latencyCost",
            "realizedExecutionCost",
            "realizedCostError",
            "incrementalRealizedNetValue",
        ),
        "model": model,
        "activationPolicy": "candidate_only_until_explicit_paper_validated_promotion",
    }
    artifact["promotionValidationGates"] = execution_cost_promotion_validation_gates(artifact)
    path = candidate_artifact_path(artifact_id)
    write_json_atomic(path, artifact)
    return {
        "status": MODEL_STATE_TRAINED_CANDIDATE,
        "artifactId": artifact_id,
        "candidateArtifactPath": str(path),
        "activeArtifactPath": str(active_artifact_path(symbol)),
        "promotionRequired": True,
        "paperTradingValidated": artifact["paperTradingValidated"],
        "outOfSampleExecutionValueProof": out_of_sample_proof,
        "promotionValidationGates": artifact["promotionValidationGates"],
        "trainingRows": len(usable),
    }


def promote_execution_cost_candidate(artifact_id: str, *, symbol: str, promoted_by: str = "manual", reason: str = "") -> dict[str, Any]:
    candidate = load_json(candidate_artifact_path(artifact_id))
    if not candidate:
        raise FileNotFoundError(f"Execution-cost candidate artifact not found: {artifact_id}")
    gate_report = execution_cost_promotion_validation_gates(candidate)
    if gate_report["passed"] is not True:
        failed = ", ".join(gate["id"] for gate in gate_report["gates"] if not gate["passed"])
        raise ValueError(f"Execution-cost candidate cannot become ACTIVE until validation gates pass: {failed}")
    active_path = active_artifact_path(symbol)
    promoted_at = utc_now_iso()
    rollback_path: Path | None = None
    previous = load_json(active_path) if active_path.exists() else None
    if previous:
        rollback_path = EXECUTION_COST_ACTIVE_HISTORY_DIR / symbol_key(symbol) / f"{safe_id(promoted_at)}_{safe_id(str(previous.get('artifactId') or 'previous'))}.json"
        write_json_atomic(rollback_path, {**previous, "lifecycleState": MODEL_STATE_RETIRED, "retiredAt": promoted_at})
    active = {
        **candidate,
        "lifecycleState": MODEL_STATE_ACTIVE,
        "promotionStatus": MODEL_STATE_ACTIVE,
        "approved": True,
        "promotedAt": promoted_at,
        "promotedBy": promoted_by,
        "promotionReason": reason,
        "promotionValidationGates": gate_report,
        "rollbackArtifactPath": str(rollback_path) if rollback_path else None,
    }
    write_json_atomic(active_path, active)
    return {
        "status": "promoted",
        "artifactId": artifact_id,
        "activePath": str(active_path),
        "rollbackArtifactPath": str(rollback_path) if rollback_path else None,
        "promotionValidationGates": gate_report,
    }


def execution_cost_candidate_validation_gate_report(artifact_id: str) -> dict[str, Any]:
    candidate = load_json(candidate_artifact_path(artifact_id))
    if not candidate:
        raise FileNotFoundError(f"Execution-cost candidate artifact not found: {artifact_id}")
    return execution_cost_promotion_validation_gates(candidate)


def execution_cost_promotion_validation_gates(candidate: dict[str, Any]) -> dict[str, Any]:
    proof = candidate.get("outOfSampleExecutionValueProof") or {}
    source_modes = {str(mode).lower() for mode in candidate.get("sourceModes") or []}
    gates = [
        validation_gate(
            "execution_cost.lifecycle.promotable_state",
            str(candidate.get("lifecycleState") or "") in {MODEL_STATE_TRAINED_CANDIDATE, MODEL_STATE_VALIDATED, MODEL_STATE_SHADOW, MODEL_STATE_PAPER_APPROVED},
            f"Candidate lifecycle state is {candidate.get('lifecycleState')}.",
        ),
        validation_gate(
            "execution_cost.data.paper_or_live_source",
            bool(source_modes & {"paper", "live"}),
            f"Source modes: {sorted(source_modes)}.",
        ),
        validation_gate(
            "execution_cost.data.minimum_paper_live_rows",
            int(candidate.get("trainingRows") or 0) >= MIN_PAPER_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL,
            f"Rows {int(candidate.get('trainingRows') or 0)} / {MIN_PAPER_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL}.",
        ),
        validation_gate(
            "execution_cost.data.minimum_paper_live_days",
            int(candidate.get("paperDays") or 0) >= MIN_PAPER_DAYS_FOR_ACTIVE_EXECUTION_COST_MODEL,
            f"Days {int(candidate.get('paperDays') or 0)} / {MIN_PAPER_DAYS_FOR_ACTIVE_EXECUTION_COST_MODEL}.",
        ),
        validation_gate(
            "execution_cost.paper_live_validation.completed",
            candidate.get("paperTradingValidated") is True,
            "Candidate must be marked paper/live validated by the training service.",
        ),
        validation_gate(
            "execution_cost.oos.positive_incremental_net_value",
            proof.get("passed") is True,
            f"OOS proof status is {proof.get('status')}; metric {proof.get('metric')}.",
        ),
        validation_gate(
            "execution_cost.oos.minimum_holdout_rows",
            int(proof.get("rows") or 0) >= MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL,
            f"OOS rows {int(proof.get('rows') or 0)} / {MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL}.",
        ),
        validation_gate(
            "execution_cost.artifact.feature_schema_hash_present",
            bool(candidate.get("featureSchemaHash")),
            "Feature schema hash must be present.",
        ),
        validation_gate(
            "execution_cost.artifact.model_buckets_present",
            bool(((candidate.get("model") or {}).get("buckets") or {})),
            "Empirical execution-cost buckets must be present.",
        ),
    ]
    passed = all(gate["passed"] for gate in gates)
    return {
        "passed": passed,
        "gateSet": "execution_cost_active_promotion_v1",
        "checkedAt": utc_now_iso(),
        "gates": gates,
        "failedGateIds": [gate["id"] for gate in gates if not gate["passed"]],
        "policy": "manual_promotion_is_allowed_only_after_all_validation_gates_pass",
    }


def validation_gate(gate_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": gate_id, "passed": bool(passed), "detail": detail}


class ExecutionCostModelService:
    def estimate(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        feature_snapshot: dict[str, Any],
        conservative_fallback: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = select_approved_execution_cost_artifact(symbol)
        if artifact is None:
            return {
                **conservative_fallback,
                "status": "CONSERVATIVE_FALLBACK_MODEL_INACTIVE",
                "modelApplied": False,
                "artifactId": None,
                "reasonCodes": ("execution_cost_model.active_artifact_unavailable",),
            }
        model = artifact.payload.get("model") or {}
        bucket = model_bucket(model, side=side, order_type=order_type)
        estimate = {
            **conservative_fallback,
            "status": "ACTIVE_MODEL",
            "modelApplied": True,
            "artifactId": artifact.payload.get("artifactId"),
            "fillProbability": bucket.get("fillProbability", conservative_fallback.get("fillProbability")),
            "limitOrderNonFillProbability": bucket.get("limitOrderNonFillProbability", conservative_fallback.get("limitOrderNonFillProbability")),
            "partialFillProbability": bucket.get("partialFillProbability", conservative_fallback.get("partialFillProbability")),
            "expectedPartialFillFraction": bucket.get("expectedPartialFillFraction", conservative_fallback.get("expectedPartialFillFraction")),
            "stopLimitMissProbability": bucket.get("stopLimitMissProbability", conservative_fallback.get("stopLimitMissProbability")),
            "realizedVsEstimatedCostErrorReserve": bucket.get("realizedCostErrorReserve", conservative_fallback.get("realizedVsEstimatedCostErrorReserve")),
            "modelFeatureSchemaHash": artifact.payload.get("featureSchemaHash"),
            "reasonCodes": ("execution_cost_model.active_artifact_applied",),
        }
        total_cost = max(
            float(estimate.get("baseCost") or 0.0),
            float(bucket.get("expectedExecutionCost") or estimate.get("totalEstimatedCost") or 0.0),
        )
        multiplier = (
            float(estimate["fillProbability"])
            * float(estimate["expectedPartialFillFraction"])
            * (1.0 - float(estimate.get("opportunityDecay") or 0.0))
        )
        estimate["totalEstimatedCost"] = round(total_cost, 6)
        estimate["expectedExecutionMultiplier"] = round(max(0.0, min(1.0, multiplier)), 4)
        return estimate


EXECUTION_COST_MODEL_SERVICE = ExecutionCostModelService()


def model_bucket(model: dict[str, Any], *, side: str, order_type: str) -> dict[str, Any]:
    buckets = model.get("buckets") or {}
    keys = [
        f"{normalize_side(side)}|{str(order_type).lower()}",
        f"{normalize_side(side)}|all",
        f"all|{str(order_type).lower()}",
        "all|all",
    ]
    for key in keys:
        if key in buckets:
            return buckets[key]
    return {}


def empirical_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {"all|all": rows}
    for row in rows:
        side = normalize_side(row.get("side"))
        order_type = str(row.get("orderType") or "limit").lower()
        buckets.setdefault(f"{side}|{order_type}", []).append(row)
        buckets.setdefault(f"{side}|all", []).append(row)
        buckets.setdefault(f"all|{order_type}", []).append(row)
    return {"buckets": {key: summarize_bucket(values) for key, values in sorted(buckets.items())}}


def chronological_execution_cost_partitions(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: str(row.get("orderSubmissionTimestamp") or row.get("decisionTimestamp") or row.get("recordedAt") or ""))
    if len(ordered) < 2:
        return {"training": ordered, "finalUntouchedTest": []}
    split = max(1, int(len(ordered) * 0.70))
    if split >= len(ordered):
        split = len(ordered) - 1
    return {"training": ordered[:split], "finalUntouchedTest": ordered[split:]}


def partition_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "start": str(rows[0].get("orderSubmissionTimestamp") or rows[0].get("decisionTimestamp") or "") if rows else None,
        "end": str(rows[-1].get("orderSubmissionTimestamp") or rows[-1].get("decisionTimestamp") or "") if rows else None,
    }


def out_of_sample_execution_value_proof(training_rows: list[dict[str, Any]], holdout_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not holdout_rows:
        return {
            "status": "insufficient_out_of_sample_rows",
            "passed": False,
            "metric": "incremental_realized_net_value_after_execution_costs",
            "reasonCodes": ("execution_cost.oos.no_final_holdout_rows",),
        }
    model = empirical_model(training_rows or holdout_rows)
    scored = [execution_value_proof_row(row, model) for row in holdout_rows]
    net_values = [float(row["incrementalRealizedNetValueAfterExecutionCosts"]) for row in scored]
    selected = [row for row in scored if row["selectedForProof"]]
    selected_net = [float(row["incrementalRealizedNetValueAfterExecutionCosts"]) for row in selected]
    rows = len(scored)
    selected_rows = len(selected)
    avg_all = mean(net_values) if net_values else 0.0
    avg_selected = mean(selected_net) if selected_net else 0.0
    total_selected = sum(selected_net)
    passed = (
        rows >= MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL
        and selected_rows > 0
        and avg_selected > 0
        and total_selected > 0
    )
    reason_codes = []
    if rows < MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL:
        reason_codes.append("execution_cost.oos.insufficient_final_holdout_rows")
    if selected_rows <= 0:
        reason_codes.append("execution_cost.oos.no_selected_holdout_rows")
    if avg_selected <= 0:
        reason_codes.append("execution_cost.oos.average_net_value_not_positive")
    if total_selected <= 0:
        reason_codes.append("execution_cost.oos.total_net_value_not_positive")
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "metric": "incremental_realized_net_value_after_execution_costs",
        "method": "chronological_final_untouched_holdout",
        "rows": rows,
        "minimumRows": MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL,
        "selectedRows": selected_rows,
        "selectedRate": round(selected_rows / max(1, rows), 4),
        "averageNetValueAllRows": round(avg_all, 6),
        "averageNetValueSelectedRows": round(avg_selected, 6) if selected_rows else 0.0,
        "totalNetValueSelectedRows": round(total_selected, 6),
        "positiveNetValueRate": round(sum(1 for value in net_values if value > 0) / max(1, len(net_values)), 4),
        "costComponents": {
            "fees": round(mean(row["fees"] for row in scored), 6) if scored else 0.0,
            "realizedExecutionCost": round(mean(row["realizedExecutionCost"] for row in scored), 6) if scored else 0.0,
            "slippageAndAdverseSelection": round(mean(row["slippageAndAdverseSelection"] for row in scored), 6) if scored else 0.0,
            "stopLimitMissPenalty": round(mean(row["stopLimitMissPenalty"] for row in scored), 6) if scored else 0.0,
            "nonFillPenalty": round(mean(row["nonFillPenalty"] for row in scored), 6) if scored else 0.0,
            "partialFillPenalty": round(mean(row["partialFillPenalty"] for row in scored), 6) if scored else 0.0,
        },
        "reasonCodes": tuple(reason_codes or ["execution_cost.oos.positive_after_costs"]),
    }


def execution_value_proof_row(row: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels") or {}
    bucket = model_bucket(model, side=str(row.get("side") or ""), order_type=str(row.get("orderType") or ""))
    filled = bool(labels.get("filled"))
    partial_fraction = clamp01(numeric(labels.get("partialFillFraction")) if filled else 0.0)
    realized_cost = numeric(labels.get("realizedExecutionCost"))
    fees = numeric(row.get("fees"))
    adverse = numeric(labels.get("adverseSelectionCost"))
    stop_miss_penalty = numeric(row.get("stopLimitMissPenalty"))
    if labels.get("stopLimitMiss") and stop_miss_penalty <= 0:
        stop_miss_penalty = max(numeric(row.get("expectedStopLimitMissCost")), numeric(bucket.get("expectedExecutionCost")) * 0.5)
    non_fill_penalty = numeric(row.get("nonFillPenalty"))
    if labels.get("nonFill") and non_fill_penalty <= 0:
        non_fill_penalty = max(numeric(row.get("opportunityCost")), numeric(bucket.get("averageIncrementalRealizedNetValue")) * 0.25)
    partial_penalty = numeric(row.get("partialFillPenalty"))
    if filled and partial_fraction < 1.0 and partial_penalty <= 0:
        partial_penalty = abs(numeric(labels.get("incrementalRealizedNetValue"))) * (1.0 - partial_fraction)
    raw_incremental = numeric(labels.get("incrementalRealizedNetValue"))
    net_value = raw_incremental - fees - stop_miss_penalty - non_fill_penalty - partial_penalty
    return {
        "selectedForProof": filled and not bool(labels.get("stopLimitMiss")),
        "incrementalRealizedNetValueAfterExecutionCosts": round(net_value, 6),
        "rawIncrementalRealizedNetValue": round(raw_incremental, 6),
        "fees": round(fees, 6),
        "realizedExecutionCost": round(realized_cost, 6),
        "slippageAndAdverseSelection": round(adverse, 6),
        "stopLimitMissPenalty": round(stop_miss_penalty, 6),
        "nonFillPenalty": round(non_fill_penalty, 6),
        "partialFillPenalty": round(partial_penalty, 6),
    }


def summarize_bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [row.get("labels") or {} for row in rows]
    costs = [numeric(label.get("realizedExecutionCost")) for label in labels]
    errors = [numeric(label.get("realizedCostError")) for label in labels]
    fill_fractions = [numeric(label.get("partialFillFraction")) for label in labels]
    adverse_selection_costs = [numeric(label.get("adverseSelectionCost")) for label in labels if label.get("adverseSelectionCost") is not None]
    return {
        "rows": len(rows),
        "fillProbability": round(rate(labels, "filled"), 4),
        "limitOrderNonFillProbability": round(rate(labels, "nonFill"), 4),
        "partialFillProbability": round(rate(labels, "partialFill"), 4),
        "expectedPartialFillFraction": round(mean(fill_fractions), 4) if fill_fractions else 1.0,
        "stopLimitMissProbability": round(rate(labels, "stopLimitMiss"), 4),
        "expectedAdverseSelectionCost": round(mean(adverse_selection_costs), 6) if adverse_selection_costs else None,
        "expectedExecutionCost": round(mean(costs), 6) if costs else 0.0,
        "realizedCostErrorReserve": round(max(0.0, mean(errors) + (pstdev(errors) if len(errors) > 1 else 0.0)), 6) if errors else 0.0,
        "averageIncrementalRealizedNetValue": round(mean(numeric(label.get("incrementalRealizedNetValue")) for label in labels), 6) if labels else 0.0,
    }


def load_observations(symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(EXECUTION_COST_LEDGER_DIR.glob(f"{symbol_key(symbol)}_*.json")):
        document = load_json(path)
        if document:
            rows.extend(record for record in document.get("records") or [] if str(record.get("symbol") or "").upper() == symbol.upper())
    return rows


def is_training_observation(row: dict[str, Any]) -> bool:
    mode = str(row.get("sourceMode") or "").lower()
    labels = row.get("labels") or {}
    return mode in {"paper", "live"} and bool(labels) and row.get("submittedQuantity") is not None


def paper_validation_passed(rows: list[dict[str, Any]], source_modes: list[str], session_days: list[str]) -> bool:
    return (
        any(mode in {"paper", "live"} for mode in source_modes)
        and len(rows) >= MIN_PAPER_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL
        and len([day for day in session_days if day]) >= MIN_PAPER_DAYS_FOR_ACTIVE_EXECUTION_COST_MODEL
    )


def rate(labels: list[dict[str, Any]], key: str) -> float:
    return sum(1 for label in labels if bool(label.get(key))) / max(1, len(labels))


def numeric(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def optional_numeric(value: Any) -> float | None:
    number = numeric(value)
    return number if number > 0 else None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def object_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {}


def json_compatible(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def midpoint(bid: Any, ask: Any) -> float | None:
    bid_value = optional_numeric(bid)
    ask_value = optional_numeric(ask)
    if bid_value is None or ask_value is None:
        return None
    return (bid_value + ask_value) / 2.0


def spread(bid: Any, ask: Any) -> float | None:
    bid_value = optional_numeric(bid)
    ask_value = optional_numeric(ask)
    if bid_value is None or ask_value is None:
        return None
    return max(0.0, ask_value - bid_value)


def inferred_order_type(intent: dict[str, Any]) -> str:
    if intent.get("triggerPrice") is not None and intent.get("limitPrice") is not None:
        return "stop_limit"
    if intent.get("limitPrice") is not None:
        return "limit"
    return "market"


def execution_source_mode(payload: dict[str, Any], intent: dict[str, Any], result: dict[str, Any]) -> str:
    explicit = payload.get("sourceMode") or payload.get("mode")
    if explicit:
        return str(explicit).lower()
    mode = str(result.get("mode") or intent.get("mode") or "").lower()
    if mode in {"manual", "automatic"}:
        return "paper"
    return mode or "unknown"


def inferred_stop_limit_miss(order_type: str, result: dict[str, Any], fill: dict[str, Any], reason_codes: tuple[str, ...]) -> bool:
    if order_type != "stop_limit":
        return False
    if any("stop_limit" in code and ("miss" in code or "not_filled" in code or "triggered_not_filled" in code) for code in reason_codes):
        return True
    status = str(result.get("status") or fill.get("status") or "").upper()
    filled_quantity = numeric(fill.get("filledQuantity") or result.get("filledQuantity"))
    return status in {"CANCELED", "EXPIRED", "ACCEPTED"} and filled_quantity <= 0


def quote_movement_between_midpoints(decision_mid: Any, submit_mid: Any, side: str) -> float | None:
    decision = optional_numeric(decision_mid)
    submitted = optional_numeric(submit_mid)
    if decision is None or submitted is None:
        return None
    movement = submitted - decision
    return round(max(0.0, movement if side == "buy" else -movement), 6)


def nearest_trade_snapshot(trades: list[dict[str, Any]], timestamp: Any) -> dict[str, Any]:
    target = parse_time(timestamp)
    if target is None or not trades:
        return {}
    best: tuple[float, dict[str, Any]] | None = None
    for trade in trades:
        trade_time = parse_time(trade.get("timestamp"))
        price = optional_numeric(trade.get("price"))
        if trade_time is None or price is None:
            continue
        distance = abs((trade_time - target).total_seconds())
        if best is None or distance < best[0]:
            best = (distance, trade)
    if best is None:
        return {}
    trade = best[1]
    return {"timestamp": trade.get("timestamp"), "price": optional_numeric(trade.get("price"))}


def normalize_side(value: Any) -> str:
    side = str(value or "").lower()
    if side in {"buy", "long"}:
        return "buy"
    if side in {"sell", "short"}:
        return "sell"
    return "unknown"


def latency_milliseconds(payload: dict[str, Any]) -> float:
    direct = payload.get("decisionToSubmissionLatencyMs")
    if direct is not None:
        return max(0.0, numeric(direct))
    decision_at = parse_time(payload.get("decisionTimestamp"))
    submitted_at = parse_time(payload.get("orderSubmissionTimestamp"))
    if decision_at and submitted_at:
        return max(0.0, (submitted_at - decision_at).total_seconds() * 1000)
    return 0.0


def latency_cost(side: str, decision_mid: float | None, submit_mid: float | None) -> float:
    if decision_mid is None or submit_mid is None:
        return 0.0
    movement = submit_mid - decision_mid
    return max(0.0, movement if side == "buy" else -movement)


def realized_execution_cost(payload: dict[str, Any], *, side: str, average_fill_price: float | None, decision_mid: float | None) -> float:
    explicit = payload.get("realizedExecutionCost")
    if explicit is not None:
        return max(0.0, numeric(explicit))
    spread = numeric(payload.get("spreadAtDecision") or payload.get("spread"))
    fees = numeric(payload.get("fees"))
    adverse = 0.0
    if average_fill_price is not None and decision_mid is not None:
        adverse = max(0.0, average_fill_price - decision_mid if side == "buy" else decision_mid - average_fill_price)
    return max(0.0, spread + fees + adverse)


def adverse_selection_cost(payload: dict[str, Any], *, side: str, average_fill_price: float | None, decision_mid: float | None) -> float | None:
    explicit = payload.get("adverseSelectionCost")
    if explicit is not None:
        return max(0.0, numeric(explicit))
    if average_fill_price is None or decision_mid is None:
        return None
    return max(0.0, average_fill_price - decision_mid if side == "buy" else decision_mid - average_fill_price)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def execution_feature_schema_hash() -> str:
    fields = [
        "symbol",
        "side",
        "orderType",
        "sourceMode",
        "submittedQuantity",
        "filledQuantity",
        "averageFillPrice",
        "midAtDecision",
        "midAtSubmit",
        "decisionToSubmissionLatencyMs",
        "spreadAtDecision",
        "fees",
        "realizedExecutionCost",
        "incrementalRealizedNetValue",
        "status",
        "submitted",
        "sourceRecordType",
    ]
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode("utf-8")).hexdigest()


__all__ = [
    "EXECUTION_COST_MODEL_SERVICE",
    "EXECUTION_COST_MODEL_VERSION",
    "MIN_PAPER_DAYS_FOR_ACTIVE_EXECUTION_COST_MODEL",
    "MIN_PAPER_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL",
    "MODEL_STATE_ACTIVE",
    "MODEL_STATE_TRAINED_CANDIDATE",
    "ExecutionCostModelService",
    "active_artifact_path",
    "candidate_artifact_path",
    "execution_cost_candidate_validation_gate_report",
    "execution_cost_promotion_validation_gates",
    "promote_execution_cost_candidate",
    "record_execution_cost_observation",
    "record_execution_cost_observation_from_order_log",
    "record_execution_cost_observations_from_order_logs",
    "select_approved_execution_cost_artifact",
    "train_execution_cost_candidate",
]
