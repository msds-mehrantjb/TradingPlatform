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
    first_candle = snapshot.candles[0].timestamp if snapshot.candles else snapshot.data_timestamp
    bar_finalization = base.bar_finalization or snapshot.data_timestamp
    snapshot_completion = base.snapshot_completion or snapshot.decision_timestamp
    merged = base.model_copy(
        update={
            "candle_open": base.candle_open or first_candle,
            "candle_close": base.candle_close or snapshot.data_timestamp,
            "bar_finalization": bar_finalization,
            "decision_start": base.decision_start or snapshot.decision_timestamp,
            "snapshot_construction_start": base.snapshot_construction_start or base.event_receipt,
            "snapshot_completion": snapshot_completion,
            "strategy_start": base.strategy_start or snapshot_completion,
            "strategy_completion": strategy_completion or base.strategy_completion,
            "aggregation_start": base.aggregation_start or strategy_completion or base.strategy_completion,
            "aggregation_completion": aggregation_completion or base.aggregation_completion,
            "risk_validation_start": base.risk_validation_start or aggregation_completion or base.aggregation_completion,
            "risk_validation_completion": global_risk_response or base.risk_validation_completion or base.global_risk_response,
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
            "outbox_queued": current.timestamps.outbox_queued or current.timestamps.decision_start,
            "outbox_reservation": outbox_reservation or current.timestamps.outbox_reservation,
            "outbox_claimed": current.timestamps.outbox_claimed or outbox_reservation,
            "broker_request": broker_request or current.timestamps.broker_request,
            "broker_acknowledgement": broker_acknowledgement or current.timestamps.broker_acknowledgement,
            "first_fill": first_fill or current.timestamps.first_fill,
            "final_fill": final_fill or current.timestamps.final_fill,
        }
    )
    metrics = _metrics_from_timestamps(timestamps, snapshot=None, base=current.metrics).model_copy(
        update={
            "slippage_per_share": slippage_per_share if slippage_per_share is not None else current.metrics.slippage_per_share,
            "fill_quality": fill_quality or current.metrics.fill_quality,
            "reason_codes": tuple(dict.fromkeys((*current.metrics.reason_codes, WCA_LATENCY_OBSERVABILITY_VERSION))),
        }
    )
    return WcaLatencySnapshot(timestamps=timestamps, metrics=metrics)


def _metrics_from_timestamps(timestamps: WcaLatencyTimestamps, snapshot: WcaMarketSnapshot | None, base: WcaLatencyMetrics | None = None) -> WcaLatencyMetrics:
    quote_age = _seconds(snapshot.quote.timestamp, snapshot.decision_timestamp) if snapshot is not None and snapshot.quote is not None else (base.quote_age_seconds if base is not None else None)
    metrics = WcaLatencyMetrics(
        candle_finalization_delay_seconds=_seconds(timestamps.candle_close, timestamps.bar_finalization),
        event_publication_delay_seconds=_seconds(timestamps.bar_finalization, timestamps.event_publication),
        queue_delay_seconds=_seconds(timestamps.event_queue_enqueued or timestamps.event_publication, timestamps.event_claimed or timestamps.event_receipt),
        event_receipt_delay_seconds=_seconds(timestamps.event_publication, timestamps.event_receipt),
        snapshot_construction_seconds=_seconds(timestamps.snapshot_construction_start, timestamps.snapshot_completion),
        strategy_evaluation_seconds=_seconds(timestamps.strategy_start, timestamps.strategy_completion),
        aggregation_seconds=_seconds(timestamps.aggregation_start, timestamps.aggregation_completion),
        risk_validation_seconds=_seconds(timestamps.risk_validation_start, timestamps.risk_validation_completion or timestamps.global_risk_response),
        outbox_delay_seconds=_seconds(timestamps.outbox_queued, timestamps.outbox_claimed or timestamps.outbox_reservation),
        broker_submission_seconds=_seconds(timestamps.broker_request, timestamps.broker_acknowledgement),
        broker_acknowledgement_seconds=_seconds(timestamps.broker_request, timestamps.broker_acknowledgement),
        fill_delay_seconds=_seconds(timestamps.broker_acknowledgement, timestamps.first_fill),
        decision_to_fill_seconds=_seconds(timestamps.decision_start, timestamps.final_fill or timestamps.first_fill),
        decision_latency_seconds=_seconds(timestamps.snapshot_completion, timestamps.aggregation_completion),
        risk_latency_seconds=_seconds(timestamps.aggregation_completion, timestamps.global_risk_response),
        processing_lag_seconds=_seconds(timestamps.bar_finalization, timestamps.snapshot_completion),
        quote_age_seconds=quote_age,
        reason_codes=(WCA_LATENCY_OBSERVABILITY_VERSION,),
    )
    if base is None:
        return metrics
    payload = base.model_dump()
    payload.update({key: value for key, value in metrics.model_dump().items() if value is not None})
    payload["reason_codes"] = tuple(dict.fromkeys((*base.reason_codes, *metrics.reason_codes)))
    return WcaLatencyMetrics.model_validate(payload)


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
