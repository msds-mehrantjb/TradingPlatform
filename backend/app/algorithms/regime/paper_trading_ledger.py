"""Passive Regime paper-trading proof ledger.

This ledger records the full decision-to-outcome chain, but it is not a live
runtime gate until a live-paper workflow explicitly enables validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


REGIME_PAPER_TRADING_LEDGER_VERSION = "regime_paper_trading_proof_ledger_v1"
REGIME_PAPER_TRADING_LEDGER_NAME = "regime_paper_trading_proof_ledger"
REGIME_PAPER_TRADING_LEDGER_DIR = Path(__file__).resolve().parents[3] / "data" / "regime" / "paper_trading_ledger"
PAPER_SOURCE_MODES = frozenset({"paper", "paper_trading", "live_paper", "live_paper_shadow"})


@dataclass(frozen=True)
class PaperTradingProofPolicy:
    minimum_records: int = 50
    minimum_completed_outcomes: int = 10
    maximum_missing_required_field_rate: float = 0.0
    maximum_latency_ms: float = 1_500.0
    maximum_cost_error: float = 0.08
    minimum_fill_rate: float = 0.70
    minimum_positive_net_value_rate: float = 0.45
    require_paper_source_mode: bool = True


def record_regime_paper_trading_proof(payload: Mapping[str, Any]) -> dict[str, Any]:
    record = normalize_paper_trading_proof_record(payload)
    path = paper_trading_ledger_path(record["symbol"], _event_day(record.get("decisionTimestamp")))
    document = _load_json(path) or {
        "ledgerVersion": REGIME_PAPER_TRADING_LEDGER_VERSION,
        "ledgerName": REGIME_PAPER_TRADING_LEDGER_NAME,
        "algorithmId": "regime",
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "recordingPolicy": "paper_trading_proof_only_until_live_paper_trading_activation",
        "symbol": record["symbol"],
        "date": _event_day(record.get("decisionTimestamp")),
        "records": [],
    }
    records = list(document.get("records") or [])
    existing = next((index for index, item in enumerate(records) if item.get("proofId") == record["proofId"]), None)
    if existing is None:
        records.append(record)
    else:
        records[existing] = {**records[existing], **record}
    records.sort(key=lambda item: str(item.get("decisionTimestamp") or item.get("eventTimestamp") or ""))
    document["records"] = records
    document["updatedAt"] = _utc_now_iso()
    _write_json_atomic(path, document)
    return {"saved": True, "path": str(path), "proofId": record["proofId"], "records": len(records)}


def read_regime_paper_trading_proof_ledger(symbol: str, *, date: str, limit: int | None = None) -> dict[str, Any]:
    path = paper_trading_ledger_path(symbol, date)
    document = _load_json(path) or {
        "ledgerVersion": REGIME_PAPER_TRADING_LEDGER_VERSION,
        "ledgerName": REGIME_PAPER_TRADING_LEDGER_NAME,
        "algorithmId": "regime",
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "symbol": symbol.upper(),
        "date": date,
        "records": [],
    }
    records = list(document.get("records") or [])
    if limit is not None:
        records = records[-max(0, int(limit)) :]
    return {**document, "records": records}


def validate_regime_paper_trading_proof_ledger(
    records: Iterable[Mapping[str, Any]],
    *,
    policy: PaperTradingProofPolicy = PaperTradingProofPolicy(),
    allow_inactive: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    normalized = [normalize_paper_trading_proof_record(record) for record in records]
    source_modes = sorted({str(record.get("sourceMode") or "unknown") for record in normalized})
    paper_source_ready = any(mode in PAPER_SOURCE_MODES for mode in source_modes)
    missing_field_rates = _missing_field_rates(normalized)
    completed = [record for record in normalized if record["realizedOutcome"]["status"] == "completed"]
    submitted = [record for record in normalized if (record["fill"]["submittedQuantity"] or 0) > 0]
    filled = [record for record in submitted if (record["fill"]["filledQuantity"] or 0) > 0]
    latency_values = [
        value
        for record in normalized
        for value in (record["latency"].get("decisionToSubmissionLatencyMs"), record["latency"].get("submissionToFillLatencyMs"))
        if value is not None
    ]
    cost_errors = [
        abs(float(record["costs"]["realizedVsEstimatedCostError"]))
        for record in normalized
        if record["costs"].get("realizedVsEstimatedCostError") is not None
    ]
    net_values = [
        float(record["realizedOutcome"]["incrementalRealizedNetValueAfterExecutionCosts"])
        for record in completed
        if record["realizedOutcome"].get("incrementalRealizedNetValueAfterExecutionCosts") is not None
    ]
    fill_rate = len(filled) / len(submitted) if submitted else 0.0
    positive_net_value_rate = sum(1 for value in net_values if value > 0) / len(net_values) if net_values else 0.0
    reason_codes: list[str] = []
    if len(normalized) < policy.minimum_records:
        reason_codes.append("regime.paper_ledger.insufficient_records")
    if len(completed) < policy.minimum_completed_outcomes:
        reason_codes.append("regime.paper_ledger.insufficient_completed_outcomes")
    if policy.require_paper_source_mode and not paper_source_ready:
        reason_codes.append("regime.paper_ledger.paper_source_mode_required")
    if any(rate > policy.maximum_missing_required_field_rate for rate in missing_field_rates.values()):
        reason_codes.append("regime.paper_ledger.required_fields_missing")
    if latency_values and mean(latency_values) > policy.maximum_latency_ms:
        reason_codes.append("regime.paper_ledger.latency_too_high")
    if cost_errors and mean(cost_errors) > policy.maximum_cost_error:
        reason_codes.append("regime.paper_ledger.cost_error_too_high")
    if fill_rate < policy.minimum_fill_rate:
        reason_codes.append("regime.paper_ledger.fill_rate_too_low")
    if positive_net_value_rate < policy.minimum_positive_net_value_rate:
        reason_codes.append("regime.paper_ledger.realized_net_value_rate_too_low")
    diagnostic_passed = not reason_codes
    validation_status = "pass" if diagnostic_passed else "fail"
    if not allow_inactive:
        validation_status = INACTIVE_UNTIL_LIVE_PAPER_TRADING
    return {
        "algorithmId": "regime",
        "ledgerName": REGIME_PAPER_TRADING_LEDGER_NAME,
        "ledgerVersion": REGIME_PAPER_TRADING_LEDGER_VERSION,
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "validationStatus": validation_status,
        "diagnosticPassed": diagnostic_passed,
        "validationAppliedToLivePaperTrading": bool(allow_inactive and diagnostic_passed),
        "recordCount": len(normalized),
        "completedOutcomeCount": len(completed),
        "sourceModes": source_modes,
        "paperSourceReady": paper_source_ready,
        "fillRate": fill_rate,
        "positiveNetValueRate": positive_net_value_rate,
        "averageDecisionToSubmissionLatencyMs": _mean_nested(normalized, "latency", "decisionToSubmissionLatencyMs"),
        "averageSubmissionToFillLatencyMs": _mean_nested(normalized, "latency", "submissionToFillLatencyMs"),
        "averageRealizedVsEstimatedCostError": mean(cost_errors) if cost_errors else None,
        "missingRequiredFieldRates": missing_field_rates,
        "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        "generatedAt": generated_at or _utc_now_iso(),
    }


def normalize_paper_trading_proof_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    decision = _object(payload.get("decision") or payload.get("decisionSnapshot"))
    fill = _object(payload.get("fill") or payload.get("actualFill"))
    costs = _object(payload.get("costs") or payload.get("executionCosts"))
    outcome = _object(payload.get("realizedOutcome") or payload.get("outcome"))
    decision_timestamp = _first(payload, "decisionTimestamp", "decisionTimestampUtc") or _first(decision, "decisionTimestamp", "timestamp")
    order_submission_timestamp = _first(payload, "orderSubmissionTimestamp", "submittedAt") or _first(fill, "orderSubmissionTimestamp", "submittedAt")
    fill_timestamp = _first(payload, "fillTimestamp", "filledAt") or _first(fill, "fillTimestamp", "filledAt")
    exit_timestamp = _first(payload, "exitTimestamp", "exitAt", "outcomeTimestamp") or _first(outcome, "exitTimestamp", "exitAt", "outcomeTimestamp")
    decision_at = _parse_time(decision_timestamp)
    submitted_at = _parse_time(order_submission_timestamp)
    filled_at = _parse_time(fill_timestamp)
    exit_at = _parse_time(exit_timestamp)
    symbol = str(_first(payload, "symbol") or _first(decision, "symbol") or _first(fill, "symbol") or "SPY").upper()
    decision_id = str(_first(payload, "decisionId", "decision_id") or _first(decision, "decisionId", "decision_id") or "")
    order_intent_id = str(_first(payload, "orderIntentId", "order_intent_id") or _first(fill, "orderIntentId", "order_intent_id") or "")
    submitted_quantity = _number(_first(fill, "submittedQuantity", "submitted_quantity", "quantity") or _first(payload, "submittedQuantity", "quantity"))
    filled_quantity = _number(_first(fill, "filledQuantity", "filled_quantity") or _first(payload, "filledQuantity"))
    estimated_cost = _number(_first(costs, "estimatedExecutionCost", "expectedExecutionCost", "estimatedCost") or _first(payload, "estimatedExecutionCost"))
    realized_cost = _number(_first(costs, "realizedExecutionCost", "actualExecutionCost", "realizedCost") or _first(payload, "realizedExecutionCost"))
    fees = _number(_first(costs, "fees") or _first(payload, "fees"))
    slippage = _number(_first(costs, "slippage") or _first(payload, "slippage"))
    gross_pnl = _number(_first(outcome, "grossPnl", "grossPnL") or _first(payload, "grossPnl", "grossPnL"))
    net_pnl = _number(_first(outcome, "netPnl", "netPnL") or _first(payload, "netPnl", "netPnL"))
    incremental_net = _number(
        _first(outcome, "incrementalRealizedNetValueAfterExecutionCosts", "incrementalRealizedNetValue")
        or _first(payload, "incrementalRealizedNetValueAfterExecutionCosts", "incrementalRealizedNetValue")
    )
    realized_vs_estimated = _number(_first(costs, "realizedVsEstimatedCostError") or _first(payload, "realizedVsEstimatedCostError"))
    if realized_vs_estimated is None and realized_cost is not None and estimated_cost is not None:
        realized_vs_estimated = realized_cost - estimated_cost
    proof_id = str(
        _first(payload, "proofId")
        or "|".join(item for item in (symbol, decision_id, order_intent_id, str(order_submission_timestamp or decision_timestamp or _utc_now_iso())) if item)
    )
    return {
        "proofId": _safe_id(proof_id),
        "ledgerVersion": REGIME_PAPER_TRADING_LEDGER_VERSION,
        "algorithmId": "regime",
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "sourceMode": str(_first(payload, "sourceMode", "mode") or "unknown").lower(),
        "symbol": symbol,
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "eventTimestamp": _iso(_parse_time(_first(payload, "eventTimestamp"))),
        "barFinalizationTimestamp": _iso(_parse_time(_first(payload, "barFinalizationTimestamp"))),
        "featureReadyTimestamp": _iso(_parse_time(_first(payload, "featureReadyTimestamp"))),
        "decisionTimestamp": _iso(decision_at),
        "orderSubmissionTimestamp": _iso(submitted_at),
        "fillTimestamp": _iso(filled_at),
        "exitTimestamp": _iso(exit_at),
        "decision": {
            "signal": _first(payload, "signal") or _first(decision, "signal"),
            "rawRegime": _first(payload, "rawRegime") or _first(decision, "rawRegime"),
            "confidence": _number(_first(payload, "confidence") or _first(decision, "confidence")),
            "tradeAllowed": _first(payload, "tradeAllowed") if "tradeAllowed" in payload else _first(decision, "tradeAllowed"),
            "blockers": tuple(_list(_first(payload, "tradeBlockers") or _first(decision, "tradeBlockers"))),
        },
        "latency": {
            "decisionToSubmissionLatencyMs": _elapsed_ms(decision_at, submitted_at),
            "submissionToFillLatencyMs": _elapsed_ms(submitted_at, filled_at),
            "decisionToFillLatencyMs": _elapsed_ms(decision_at, filled_at),
            "holdingPeriodMs": _elapsed_ms(filled_at, exit_at),
        },
        "costs": {
            "estimatedExecutionCost": estimated_cost,
            "realizedExecutionCost": realized_cost,
            "realizedVsEstimatedCostError": round(realized_vs_estimated, 8) if realized_vs_estimated is not None else None,
            "fees": fees,
            "slippage": slippage,
        },
        "fill": {
            "status": str(_first(fill, "status") or _first(payload, "fillStatus") or "").upper() or None,
            "submittedQuantity": submitted_quantity,
            "filledQuantity": filled_quantity,
            "partialFillFraction": (filled_quantity / submitted_quantity) if submitted_quantity and submitted_quantity > 0 and filled_quantity is not None else None,
            "averageFillPrice": _number(_first(fill, "averageFillPrice", "average_fill_price") or _first(payload, "averageFillPrice")),
            "brokerOrderId": _first(fill, "brokerOrderId", "broker_order_id") or _first(payload, "brokerOrderId"),
            "clientOrderId": _first(fill, "clientOrderId", "client_order_id") or _first(payload, "clientOrderId"),
        },
        "realizedOutcome": {
            "status": str(_first(outcome, "status") or ("completed" if exit_at else "pending")).lower(),
            "exitReason": _first(outcome, "exitReason", "exit_reason") or _first(payload, "exitReason"),
            "exitPrice": _number(_first(outcome, "exitPrice", "exit_price") or _first(payload, "exitPrice")),
            "grossPnl": gross_pnl,
            "netPnl": net_pnl,
            "incrementalRealizedNetValueAfterExecutionCosts": incremental_net,
        },
        "requiredProofFieldsPresent": _required_proof_fields_present(decision_at, submitted_at, estimated_cost, realized_cost, fill, outcome),
        "recordedAt": _utc_now_iso(),
    }


def paper_trading_ledger_path(symbol: str, day: str) -> Path:
    return REGIME_PAPER_TRADING_LEDGER_DIR / f"{_safe_id(symbol.upper())}_{day}.json"


def _required_proof_fields_present(
    decision_at: datetime | None,
    submitted_at: datetime | None,
    estimated_cost: float | None,
    realized_cost: float | None,
    fill: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, bool]:
    return {
        "decisionTimestamp": decision_at is not None,
        "orderSubmissionTimestamp": submitted_at is not None,
        "estimatedExecutionCost": estimated_cost is not None,
        "realizedExecutionCost": realized_cost is not None,
        "fillLifecycle": bool(fill),
        "realizedOutcome": bool(outcome),
    }


def _missing_field_rates(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {}
    fields = sorted({field for record in records for field in record.get("requiredProofFieldsPresent", {})})
    return {
        field: sum(1 for record in records if not record.get("requiredProofFieldsPresent", {}).get(field)) / len(records)
        for field in fields
    }


def _mean_nested(records: list[dict[str, Any]], parent: str, key: str) -> float | None:
    values = [record.get(parent, {}).get(key) for record in records if record.get(parent, {}).get(key) is not None]
    return mean(float(value) for value in values) if values else None


def _elapsed_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds() * 1000)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else list(value) if isinstance(value, tuple) else []


def _first(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_day(timestamp: str | None) -> str:
    return str(timestamp or _utc_now_iso())[:10]


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in str(value))[:180] or "record"
