"""WCA latency observability helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.algorithms.wca.contracts import WcaLatencyMetrics, WcaLatencySnapshot, WcaLatencyTimestamps, WcaMarketSnapshot


WCA_LATENCY_OBSERVABILITY_VERSION = "wca_latency_observability_v1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_decision_latency_snapshot(
    *,
    snapshot: WcaMarketSnapshot,
    timestamps: WcaLatencyTimestamps | None = None,
    strategy_completion: datetime | None = None,
    aggregation_completion: datetime | None = None,
    global_risk_response: datetime | None = None,
) -> WcaLatencySnapshot:
    base = timestamps or WcaLatencyTimestamps()
    bar_finalization = base.bar_finalization or snapshot.data_timestamp
    snapshot_completion = base.snapshot_completion or snapshot.decision_timestamp
    merged = base.model_copy(
        update={
            "bar_finalization": bar_finalization,
            "snapshot_completion": snapshot_completion,
            "strategy_completion": strategy_completion or base.strategy_completion,
            "aggregation_completion": aggregation_completion or base.aggregation_completion,
            "global_risk_response": global_risk_response or base.global_risk_response,
        }
    )
    metrics = _metrics_from_timestamps(merged, snapshot)
    return WcaLatencySnapshot(timestamps=merged, metrics=metrics)


def with_order_latency(
    latency: WcaLatencySnapshot | None,
    *,
    outbox_reservation: datetime | None = None,
    broker_request: datetime | None = None,
    broker_acknowledgement: datetime | None = None,
    first_fill: datetime | None = None,
    final_fill: datetime | None = None,
    slippage_per_share: float | None = None,
    fill_quality: str | None = None,
) -> WcaLatencySnapshot:
    current = latency or WcaLatencySnapshot()
    timestamps = current.timestamps.model_copy(
        update={
            "outbox_reservation": outbox_reservation or current.timestamps.outbox_reservation,
            "broker_request": broker_request or current.timestamps.broker_request,
            "broker_acknowledgement": broker_acknowledgement or current.timestamps.broker_acknowledgement,
            "first_fill": first_fill or current.timestamps.first_fill,
            "final_fill": final_fill or current.timestamps.final_fill,
        }
    )
    metrics = current.metrics.model_copy(
        update={
            "broker_latency_seconds": _seconds(timestamps.broker_request, timestamps.broker_acknowledgement),
            "slippage_per_share": slippage_per_share if slippage_per_share is not None else current.metrics.slippage_per_share,
            "fill_quality": fill_quality or current.metrics.fill_quality,
            "reason_codes": tuple(dict.fromkeys((*current.metrics.reason_codes, WCA_LATENCY_OBSERVABILITY_VERSION))),
        }
    )
    return WcaLatencySnapshot(timestamps=timestamps, metrics=metrics)


def _metrics_from_timestamps(timestamps: WcaLatencyTimestamps, snapshot: WcaMarketSnapshot) -> WcaLatencyMetrics:
    quote_age = _seconds(snapshot.quote.timestamp, snapshot.decision_timestamp) if snapshot.quote is not None else None
    return WcaLatencyMetrics(
        decision_latency_seconds=_seconds(timestamps.snapshot_completion, timestamps.aggregation_completion),
        risk_latency_seconds=_seconds(timestamps.aggregation_completion, timestamps.global_risk_response),
        processing_lag_seconds=_seconds(timestamps.bar_finalization, timestamps.snapshot_completion),
        quote_age_seconds=quote_age,
        reason_codes=(WCA_LATENCY_OBSERVABILITY_VERSION,),
    )


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds())


__all__ = [
    "WCA_LATENCY_OBSERVABILITY_VERSION",
    "build_decision_latency_snapshot",
    "utc_now",
    "with_order_latency",
]
