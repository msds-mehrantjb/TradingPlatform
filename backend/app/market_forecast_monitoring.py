from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any

from .execution.cost_model import load_observations
from .market_forecast import read_market_forecast_prediction_log


MONITORING_VERSION = "market_forecast_monitoring_v1"
MONITORING_POLICY = "read_only_alerts_do_not_authorize_orders"

DEFAULT_ALERT_CONFIG = {
    "minSamples": 6,
    "latencyWarningMs": 1000.0,
    "latencyCriticalMs": 2500.0,
    "latencyDriftRatio": 2.0,
    "costErrorWarning": 0.03,
    "costErrorCritical": 0.08,
    "costErrorDrift": 0.03,
    "calibrationBrierWarning": 0.30,
    "calibrationBrierCritical": 0.40,
    "calibrationBrierDrift": 0.08,
    "fillRateWarning": 0.70,
    "fillRateCritical": 0.50,
    "fillRateDeterioration": 0.15,
    "regimeMinSamples": 3,
    "regimeWinRateWarning": 0.45,
    "regimeWinRateCritical": 0.30,
    "regimeNetValueWarning": 0.0,
}


def market_forecast_monitoring_alerts(
    symbol: str = "SPY",
    *,
    limit: int = 500,
    forecast_records: list[dict[str, Any]] | None = None,
    execution_records: list[dict[str, Any]] | None = None,
    config: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    """Return passive monitoring alerts over forecast and execution-quality ledgers."""

    settings = {**DEFAULT_ALERT_CONFIG, **(config or {})}
    symbol = symbol.upper()
    forecasts = chronological_records(
        forecast_records
        if forecast_records is not None
        else read_market_forecast_prediction_log(symbol, limit=limit).get("records") or [],
        timestamp_keys=("predictionTimestamp", "eventTimestamp", "generatedAt"),
    )[-limit:]
    executions = chronological_records(
        execution_records if execution_records is not None else load_observations(symbol),
        timestamp_keys=("decisionTimestamp", "orderSubmissionTimestamp", "recordedAt"),
    )[-limit:]

    alerts: list[dict[str, Any]] = []
    alerts.extend(latency_drift_alerts(executions, forecasts, settings))
    alerts.extend(cost_estimate_error_alerts(executions, forecasts, settings))
    alerts.extend(calibration_drift_alerts(forecasts, settings))
    alerts.extend(fill_rate_deterioration_alerts(executions, settings))
    alerts.extend(regime_specific_failure_alerts(forecasts, executions, settings))

    return {
        "status": "ALERTS_PRESENT" if alerts else "NO_ALERTS",
        "symbol": symbol,
        "monitoringVersion": MONITORING_VERSION,
        "activationPolicy": MONITORING_POLICY,
        "alerts": alerts,
        "summary": {
            "forecastRecords": len(forecasts),
            "executionRecords": len(executions),
            "resolvedForecastRecords": sum(1 for row in forecasts if is_resolved_forecast(row)),
            "executionCostRecordsWithLabels": sum(1 for row in executions if bool(row.get("labels"))),
            "monitoredRisks": [
                "latency_drift",
                "cost_estimate_error",
                "calibration_drift",
                "fill_rate_deterioration",
                "regime_specific_failure",
            ],
        },
    }


def latency_drift_alerts(
    execution_records: list[dict[str, Any]],
    forecast_records: list[dict[str, Any]],
    config: dict[str, float | int],
) -> list[dict[str, Any]]:
    values = [value for value in (execution_latency_ms(row) for row in execution_records) if value is not None]
    if not values:
        values = [value for value in (forecast_decision_latency_ms(row) for row in forecast_records) if value is not None]
    split = baseline_recent(values, int(config["minSamples"]))
    if not split:
        return []
    baseline, recent = split
    baseline_avg = mean(baseline) if baseline else 0.0
    recent_avg = mean(recent)
    recent_p95 = percentile(recent, 0.95)
    warning = float(config["latencyWarningMs"])
    critical = float(config["latencyCriticalMs"])
    ratio = float(config["latencyDriftRatio"])
    drifted = baseline_avg > 0 and recent_avg >= baseline_avg * ratio
    if recent_p95 < warning and not drifted:
        return []
    severity = "critical" if recent_p95 >= critical else "warning"
    return [
        alert(
            "monitoring.latency_drift",
            severity,
            "Decision-to-submission or forecast pipeline latency drifted above the expected operating envelope.",
            "latency_p95_ms",
            recent_p95,
            threshold=critical if severity == "critical" else warning,
            baseline=baseline_avg,
            sample_count=len(recent),
        )
    ]


def cost_estimate_error_alerts(
    execution_records: list[dict[str, Any]],
    forecast_records: list[dict[str, Any]],
    config: dict[str, float | int],
) -> list[dict[str, Any]]:
    values = [abs(value) for value in (execution_cost_error(row) for row in execution_records) if value is not None]
    if not values:
        values = [abs(value) for value in (forecast_cost_error(row) for row in forecast_records) if value is not None]
    split = baseline_recent(values, int(config["minSamples"]))
    if not split:
        return []
    baseline, recent = split
    baseline_avg = mean(baseline) if baseline else 0.0
    recent_avg = mean(recent)
    warning = float(config["costErrorWarning"])
    critical = float(config["costErrorCritical"])
    drift = float(config["costErrorDrift"])
    if recent_avg < warning and recent_avg < baseline_avg + drift:
        return []
    severity = "critical" if recent_avg >= critical else "warning"
    return [
        alert(
            "monitoring.cost_estimate_error",
            severity,
            "Realized execution costs are diverging from estimated costs.",
            "mean_absolute_realized_cost_error",
            recent_avg,
            threshold=critical if severity == "critical" else warning,
            baseline=baseline_avg,
            sample_count=len(recent),
        )
    ]


def calibration_drift_alerts(forecast_records: list[dict[str, Any]], config: dict[str, float | int]) -> list[dict[str, Any]]:
    scored = [row for row in (forecast_calibration_row(record) for record in forecast_records) if row is not None]
    split = baseline_recent(scored, int(config["minSamples"]))
    if not split:
        return []
    baseline_rows, recent_rows = split
    baseline_brier = brier_score(baseline_rows) if baseline_rows else 0.0
    recent_brier = brier_score(recent_rows)
    warning = float(config["calibrationBrierWarning"])
    critical = float(config["calibrationBrierCritical"])
    drift = float(config["calibrationBrierDrift"])
    if recent_brier < warning and recent_brier < baseline_brier + drift:
        return []
    severity = "critical" if recent_brier >= critical else "warning"
    return [
        alert(
            "monitoring.calibration_drift",
            severity,
            "Forecast probabilities are no longer matching realized outcomes.",
            "brier_score",
            recent_brier,
            threshold=critical if severity == "critical" else warning,
            baseline=baseline_brier,
            sample_count=len(recent_rows),
        )
    ]


def fill_rate_deterioration_alerts(
    execution_records: list[dict[str, Any]],
    config: dict[str, float | int],
) -> list[dict[str, Any]]:
    labeled = [row for row in execution_records if isinstance(row.get("labels"), dict)]
    split = baseline_recent(labeled, int(config["minSamples"]))
    if not split:
        return []
    baseline, recent = split
    baseline_rate = fill_rate(baseline) if baseline else 1.0
    recent_rate = fill_rate(recent)
    warning = float(config["fillRateWarning"])
    critical = float(config["fillRateCritical"])
    deterioration = float(config["fillRateDeterioration"])
    if recent_rate >= warning and recent_rate >= baseline_rate - deterioration:
        return []
    severity = "critical" if recent_rate <= critical else "warning"
    return [
        alert(
            "monitoring.fill_rate_deterioration",
            severity,
            "Order fill rate deteriorated; non-fills, partial fills, or missed stop-limit exits need review.",
            "fill_rate",
            recent_rate,
            threshold=critical if severity == "critical" else warning,
            baseline=baseline_rate,
            sample_count=len(recent),
        )
    ]


def regime_specific_failure_alerts(
    forecast_records: list[dict[str, Any]],
    execution_records: list[dict[str, Any]],
    config: dict[str, float | int],
) -> list[dict[str, Any]]:
    rows_by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in forecast_records:
        if not is_resolved_forecast(record):
            continue
        regime = regime_label(record)
        if not regime:
            continue
        rows_by_regime[regime].append(record)

    for row in execution_records:
        labels = row.get("labels") or {}
        regime = regime_label(row)
        if labels and regime:
            rows_by_regime[regime].append(row)

    alerts: list[dict[str, Any]] = []
    min_samples = int(config["regimeMinSamples"])
    warning_win_rate = float(config["regimeWinRateWarning"])
    critical_win_rate = float(config["regimeWinRateCritical"])
    net_threshold = float(config["regimeNetValueWarning"])
    for regime, rows in sorted(rows_by_regime.items()):
        recent = chronological_records(rows, timestamp_keys=("predictionTimestamp", "decisionTimestamp", "recordedAt"))[-max(min_samples, int(config["minSamples"])) :]
        if len(recent) < min_samples:
            continue
        win_rate_value = realized_win_rate(recent)
        net_values = [value for value in (incremental_net_value(row) for row in recent) if value is not None]
        avg_net = mean(net_values) if net_values else None
        failed_by_win_rate = win_rate_value is not None and win_rate_value < warning_win_rate
        failed_by_net = avg_net is not None and avg_net < net_threshold
        if not failed_by_win_rate and not failed_by_net:
            continue
        severity = "critical" if win_rate_value is not None and win_rate_value <= critical_win_rate else "warning"
        alerts.append(
            alert(
                "monitoring.regime_specific_failure",
                severity,
                f"Forecast or execution performance is failing in regime '{regime}'.",
                "regime_win_rate" if failed_by_win_rate else "regime_average_incremental_net_value",
                win_rate_value if failed_by_win_rate else avg_net,
                threshold=critical_win_rate if severity == "critical" else warning_win_rate if failed_by_win_rate else net_threshold,
                baseline=None,
                sample_count=len(recent),
                dimensions={"regime": regime, "averageIncrementalNetValue": avg_net, "winRate": win_rate_value},
            )
        )
    return alerts


def alert(
    alert_id: str,
    severity: str,
    message: str,
    metric: str,
    recent: float | None,
    *,
    threshold: float,
    baseline: float | None,
    sample_count: int,
    dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": alert_id,
        "severity": severity,
        "status": "ACTIVE",
        "message": message,
        "metric": metric,
        "recentValue": round(recent, 6) if isinstance(recent, float) and math.isfinite(recent) else recent,
        "baselineValue": round(baseline, 6) if isinstance(baseline, float) and math.isfinite(baseline) else baseline,
        "threshold": threshold,
        "sampleCount": sample_count,
        "dimensions": dimensions or {},
        "activationPolicy": MONITORING_POLICY,
    }


def baseline_recent(values: list[Any], min_samples: int) -> tuple[list[Any], list[Any]] | None:
    if len(values) < max(2, min_samples):
        return None
    recent_size = max(max(1, min_samples // 2), min(len(values) // 3, len(values) - 1))
    recent = values[-recent_size:]
    baseline = values[:-recent_size]
    if not recent:
        return None
    return baseline, recent


def chronological_records(records: list[dict[str, Any]], *, timestamp_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda row: first_timestamp(row, timestamp_keys) or "")


def first_timestamp(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value)
    return None


def execution_latency_ms(row: dict[str, Any]) -> float | None:
    direct = optional_float(row.get("decisionToSubmissionLatencyMs"))
    if direct is not None:
        return direct
    return elapsed_ms(row.get("decisionTimestamp"), row.get("orderSubmissionTimestamp"))


def forecast_decision_latency_ms(row: dict[str, Any]) -> float | None:
    return elapsed_ms(row.get("eventTimestamp") or row.get("barFinalizationTimestamp"), row.get("decisionTimestamp"))


def execution_cost_error(row: dict[str, Any]) -> float | None:
    labels = row.get("labels") or {}
    return optional_float(labels.get("realizedCostError"))


def forecast_cost_error(row: dict[str, Any]) -> float | None:
    actual = row.get("actual") or {}
    return optional_float(actual.get("realizedVersusEstimatedCostError"))


def forecast_calibration_row(record: dict[str, Any]) -> dict[str, float] | None:
    actual = record.get("actual") or {}
    if actual.get("status") != "resolved":
        return None
    probability = selected_forecast_probability(record)
    if probability is None:
        return None
    success = actual_success(record)
    if success is None:
        return None
    return {"probability": probability, "actual": 1.0 if success else 0.0}


def selected_forecast_probability(record: dict[str, Any]) -> float | None:
    prediction = record.get("prediction") or {}
    probabilities = record.get("predictionProbabilities") or {}
    action = str(prediction.get("decisionAction") or prediction.get("candidateAction") or "").lower()
    key = "probabilityBuySuccess" if action == "buy" else "probabilitySellSuccess" if action == "sell" else None
    if not key:
        return None
    return optional_probability(probabilities.get(key) if key in probabilities else prediction.get(key))


def actual_success(record: dict[str, Any]) -> bool | None:
    actual = record.get("actual") or {}
    if isinstance(actual.get("executedDecisionCorrect"), bool):
        return bool(actual["executedDecisionCorrect"])
    trade_result = str(actual.get("tradeResult") or "").lower()
    if trade_result in {"win", "winner", "profit"}:
        return True
    if trade_result in {"loss", "loser", "stop", "timeout"}:
        return False
    outcome = str(actual.get("realizedOutcome") or actual.get("outcome") or "").lower()
    if outcome in {"target", "profit_target", "profit_target_hit_first"}:
        return True
    if outcome in {"stop", "timeout", "stop_loss", "stop_loss_hit_first"}:
        return False
    return None


def brier_score(rows: list[dict[str, float]]) -> float:
    return mean((row["probability"] - row["actual"]) ** 2 for row in rows)


def fill_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 1.0
    return sum(1 for row in rows if bool((row.get("labels") or {}).get("filled"))) / len(rows)


def realized_win_rate(rows: list[dict[str, Any]]) -> float | None:
    outcomes = []
    for row in rows:
        success = actual_success(row)
        if success is None:
            labels = row.get("labels") or {}
            if labels:
                success = numeric(labels.get("incrementalRealizedNetValue")) > 0
        if success is not None:
            outcomes.append(bool(success))
    if not outcomes:
        return None
    return sum(1 for outcome in outcomes if outcome) / len(outcomes)


def incremental_net_value(row: dict[str, Any]) -> float | None:
    actual = row.get("actual") or {}
    value = optional_float(actual.get("incrementalRealizedNetValueAfterExecutionCosts"))
    if value is not None:
        return value
    labels = row.get("labels") or {}
    return optional_float(labels.get("incrementalRealizedNetValue"))


def regime_label(row: dict[str, Any]) -> str | None:
    for container_key in ("marketRegime", "regime", "regimeContext"):
        container = row.get(container_key)
        if isinstance(container, str) and container.strip():
            return container.strip().lower()
        if not isinstance(container, dict):
            continue
        for key in ("label", "name", "regime", "marketRegime", "phase", "structure", "trendStrength", "volatilityLevel"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    value = row.get("regimeLabel")
    return str(value).strip().lower() if value else None


def is_resolved_forecast(row: dict[str, Any]) -> bool:
    return (row.get("actual") or {}).get("status") == "resolved"


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def elapsed_ms(start: Any, end: Any) -> float | None:
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if not start_dt or not end_dt:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() * 1000.0)


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def optional_probability(value: Any) -> float | None:
    number = optional_float(value)
    if number is None:
        return None
    return min(1.0, max(0.0, number))


def optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def numeric(value: Any) -> float:
    number = optional_float(value)
    return number if number is not None else 0.0
