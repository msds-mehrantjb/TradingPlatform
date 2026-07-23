"""Passive Regime monitoring alerts by market condition."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping

from backend.app.algorithms.regime.paper_trading_ledger import normalize_paper_trading_proof_record
from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


REGIME_CONDITION_MONITORING_VERSION = "regime_condition_monitoring_v1"
REGIME_CONDITION_MONITORING_POLICY = "passive_alerts_until_paper_trading_activation"


@dataclass(frozen=True)
class RegimeConditionMonitoringPolicy:
    minimum_samples_per_condition: int = 6
    win_rate_warning: float = 0.45
    win_rate_critical: float = 0.30
    minimum_average_incremental_net_value: float = 0.0
    maximum_average_cost_error: float = 0.05
    maximum_average_latency_ms: float = 1_500.0
    minimum_fill_rate: float = 0.70
    maximum_block_rate: float = 0.35


def regime_condition_monitoring_alerts(
    records: Iterable[Mapping[str, Any]],
    *,
    policy: RegimeConditionMonitoringPolicy = RegimeConditionMonitoringPolicy(),
    allow_inactive: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    normalized = [_monitoring_record(record) for record in records]
    grouped = _records_by_condition(normalized)
    alerts: list[dict[str, Any]] = []
    condition_summaries: dict[str, dict[str, Any]] = {}

    for condition_key, rows in sorted(grouped.items()):
        if len(rows) < policy.minimum_samples_per_condition:
            continue
        summary = _condition_summary(condition_key, rows)
        condition_summaries[condition_key] = summary
        alerts.extend(_condition_alerts(summary, policy))

    activation_status = INACTIVE_UNTIL_LIVE_PAPER_TRADING
    monitor_status = "ALERTS_PRESENT" if alerts else "NO_ALERTS"
    if not allow_inactive:
        monitor_status = activation_status

    return {
        "algorithmId": "regime",
        "monitoringVersion": REGIME_CONDITION_MONITORING_VERSION,
        "activationStatus": activation_status,
        "monitoringStatus": monitor_status,
        "alertsAppliedToPaperTrading": bool(allow_inactive and alerts),
        "recordCount": len(normalized),
        "conditionCount": len(condition_summaries),
        "minimumSamplesPerCondition": policy.minimum_samples_per_condition,
        "alerts": alerts,
        "conditionSummaries": condition_summaries,
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
    }


def _records_by_condition(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for condition in _condition_keys(record):
            grouped[condition].append(record)
    return grouped


def _monitoring_record(record: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_paper_trading_proof_record(record)
    for key in ("axes", "condition", "evidence", "rawRegime", "direction", "volatility", "structure", "liquidity", "session", "eventRisk", "event_risk"):
        if key in record:
            normalized[key] = record[key]
    return normalized


def _condition_keys(record: Mapping[str, Any]) -> tuple[str, ...]:
    decision = record.get("decision") if isinstance(record.get("decision"), Mapping) else {}
    condition = record.get("condition") if isinstance(record.get("condition"), Mapping) else {}
    axes = record.get("axes") if isinstance(record.get("axes"), Mapping) else {}
    evidence = record.get("evidence") if isinstance(record.get("evidence"), Mapping) else {}
    confidence = evidence.get("confidenceEvidence") if isinstance(evidence.get("confidenceEvidence"), Mapping) else {}
    keys: list[str] = []
    raw_regime = _text(record.get("rawRegime") or decision.get("rawRegime") or condition.get("rawRegime") or condition.get("regime"))
    if raw_regime:
        keys.append(f"regime:{raw_regime}")
    for axis in ("direction", "volatility", "structure", "liquidity", "session", "eventRisk", "event_risk"):
        value = _text(record.get(axis) or condition.get(axis) or axes.get(axis))
        if value:
            keys.append(f"{axis.replace('_', '')}:{value}")
    if confidence:
        for axis in ("directionConfidence", "volatilityConfidence", "structureConfidence", "liquidityConfidence", "eventConfidence"):
            value = _number(confidence.get(axis))
            if value is not None and value < 0.50:
                keys.append(f"low_confidence:{axis}")
    return tuple(dict.fromkeys(keys or ("condition:unknown",)))


def _condition_summary(condition_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("realizedOutcome", {}).get("status") == "completed"]
    submitted = [row for row in rows if _number(row.get("fill", {}).get("submittedQuantity")) and _number(row.get("fill", {}).get("submittedQuantity")) > 0]
    filled = [row for row in submitted if _number(row.get("fill", {}).get("filledQuantity")) and _number(row.get("fill", {}).get("filledQuantity")) > 0]
    net_values = [
        value
        for value in (_number(row.get("realizedOutcome", {}).get("incrementalRealizedNetValueAfterExecutionCosts")) for row in completed)
        if value is not None
    ]
    wins = [value > 0 for value in net_values]
    cost_errors = [
        abs(value)
        for value in (_number(row.get("costs", {}).get("realizedVsEstimatedCostError")) for row in rows)
        if value is not None
    ]
    latencies = [
        value
        for row in rows
        for value in (
            _number(row.get("latency", {}).get("decisionToSubmissionLatencyMs")),
            _number(row.get("latency", {}).get("submissionToFillLatencyMs")),
        )
        if value is not None
    ]
    blocked = [row for row in rows if row.get("decision", {}).get("tradeAllowed") is False or bool(row.get("decision", {}).get("blockers"))]
    return {
        "condition": condition_key,
        "sampleCount": len(rows),
        "completedOutcomeCount": len(completed),
        "winRate": (sum(1 for win in wins if win) / len(wins)) if wins else None,
        "averageIncrementalNetValue": mean(net_values) if net_values else None,
        "averageAbsoluteCostError": mean(cost_errors) if cost_errors else None,
        "averageLatencyMs": mean(latencies) if latencies else None,
        "fillRate": (len(filled) / len(submitted)) if submitted else None,
        "blockRate": len(blocked) / len(rows) if rows else 0.0,
        "topBlockers": _top_blockers(rows),
    }


def _condition_alerts(summary: Mapping[str, Any], policy: RegimeConditionMonitoringPolicy) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    condition = str(summary["condition"])
    win_rate = _number(summary.get("winRate"))
    avg_net = _number(summary.get("averageIncrementalNetValue"))
    cost_error = _number(summary.get("averageAbsoluteCostError"))
    latency = _number(summary.get("averageLatencyMs"))
    fill_rate = _number(summary.get("fillRate"))
    block_rate = _number(summary.get("blockRate"))
    if win_rate is not None and win_rate < policy.win_rate_warning:
        alerts.append(_alert("regime.monitor.condition_win_rate_failure", "critical" if win_rate <= policy.win_rate_critical else "warning", condition, "win_rate", win_rate, policy.win_rate_critical if win_rate <= policy.win_rate_critical else policy.win_rate_warning, summary))
    if avg_net is not None and avg_net < policy.minimum_average_incremental_net_value:
        alerts.append(_alert("regime.monitor.condition_negative_net_value", "critical" if avg_net < 0 else "warning", condition, "average_incremental_net_value", avg_net, policy.minimum_average_incremental_net_value, summary))
    if cost_error is not None and cost_error > policy.maximum_average_cost_error:
        alerts.append(_alert("regime.monitor.condition_cost_error", "warning", condition, "average_absolute_cost_error", cost_error, policy.maximum_average_cost_error, summary))
    if latency is not None and latency > policy.maximum_average_latency_ms:
        alerts.append(_alert("regime.monitor.condition_latency_drift", "warning", condition, "average_latency_ms", latency, policy.maximum_average_latency_ms, summary))
    if fill_rate is not None and fill_rate < policy.minimum_fill_rate:
        alerts.append(_alert("regime.monitor.condition_fill_rate_deterioration", "critical" if fill_rate < 0.50 else "warning", condition, "fill_rate", fill_rate, policy.minimum_fill_rate, summary))
    if block_rate is not None and block_rate > policy.maximum_block_rate:
        alerts.append(_alert("regime.monitor.condition_block_rate", "warning", condition, "block_rate", block_rate, policy.maximum_block_rate, summary))
    return alerts


def _alert(
    alert_id: str,
    severity: str,
    condition: str,
    metric: str,
    value: float,
    threshold: float,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": alert_id,
        "severity": severity,
        "status": "ACTIVE",
        "condition": condition,
        "metric": metric,
        "recentValue": round(value, 6),
        "threshold": threshold,
        "sampleCount": int(summary.get("sampleCount") or 0),
        "dimensions": {
            "condition": condition,
            "completedOutcomeCount": summary.get("completedOutcomeCount"),
            "winRate": summary.get("winRate"),
            "averageIncrementalNetValue": summary.get("averageIncrementalNetValue"),
            "averageAbsoluteCostError": summary.get("averageAbsoluteCostError"),
            "averageLatencyMs": summary.get("averageLatencyMs"),
            "fillRate": summary.get("fillRate"),
            "blockRate": summary.get("blockRate"),
            "topBlockers": summary.get("topBlockers"),
        },
        "activationPolicy": REGIME_CONDITION_MONITORING_POLICY,
    }


def _top_blockers(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("decision", {}).get("blockers") or ():
            counts[str(blocker)] = counts.get(str(blocker), 0) + 1
    return tuple({"blocker": blocker, "count": count} for blocker, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5])


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
