"""Dedicated raw-market snapshot adapter for Weighted Voting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedMarketSnapshot,
    WeightedReferenceLevels,
    WeightedSessionPhase,
    WeightedSnapshotInputSet,
)


WEIGHTED_VOTING_MARKET_SNAPSHOT_ADAPTER_VERSION = "weighted_voting_market_snapshot_adapter_v1"

FORBIDDEN_FOREIGN_ALGORITHM_FIELDS = (
    "votingEnsemble",
    "voting_ensemble",
    "votingEnsembleDecision",
    "wca",
    "wcaDecision",
    "confidenceAggregation",
    "confidence_aggregation",
    "regimeBasedTrading",
    "regime_based_trading",
    "regimeSelection",
    "metaModel",
    "meta_model",
    "metaStrategy",
    "otherAlgorithmConfidenceScores",
    "other_algorithm_confidence_scores",
    "otherAlgorithmPositions",
    "other_algorithm_positions",
    "otherAlgorithmSettings",
    "other_algorithm_settings",
    "dynamicTradingArtifact",
)

WeightedVotingCandle = WeightedCandle
WeightedVotingMarketSnapshot = WeightedMarketSnapshot


def build_weighted_voting_market_snapshot(payload: dict[str, Any]) -> WeightedVotingMarketSnapshot:
    """Convert shared raw market data into Weighted Voting's immutable contract.

    The adapter intentionally selects only market facts. Foreign algorithm
    decisions, confidence scores, positions, and settings are ignored even when
    present in the same shared payload.
    """

    candles = _candles_from_rows(payload.get("candles") or payload.get("one_minute_candles") or payload.get("oneMinuteCandles") or ())
    if not candles:
        raise ValueError("candles are required")
    five_minute_candles = _candles_from_rows(payload.get("five_minute_candles") or payload.get("fiveMinuteCandles") or ())
    timestamp = _parse_datetime(payload.get("data_timestamp") or payload.get("dataTimestamp") or payload.get("decision_timestamp") or payload.get("decisionTimestamp") or candles[-1].timestamp)
    bid = _optional_float(payload.get("bid"))
    ask = _optional_float(payload.get("ask"))
    if bid is None:
        bid = max(0.01, candles[-1].close - 0.01)
    if ask is None:
        ask = candles[-1].close + 0.01
    spread = max(0.0, ask - bid)
    session_info = _object_value(payload.get("session") or payload.get("session_information") or payload.get("sessionInformation"))
    snapshot_values = {
        "symbol": str(payload.get("symbol") or "SPY"),
        "decision_timestamp": timestamp,
        "data_timestamp": timestamp,
        "one_minute_candles": candles,
        "five_minute_candles": five_minute_candles,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "session_date": _session_date(payload, session_info, timestamp),
        "session_label": str(payload.get("session_label") or payload.get("sessionLabel") or session_info.get("label") or session_info.get("session_label") or "unknown"),
        "session_phase": _session_phase(payload, session_info),
        "opening_range_levels": _reference_levels(payload.get("opening_range_levels") or payload.get("openingRangeLevels")) or _derived_opening_range(candles),
        "previous_day_levels": _reference_levels(payload.get("previous_day_levels") or payload.get("previousDayLevels") or payload.get("prior_day_levels") or payload.get("priorDayLevels")),
        "vwap_inputs": _input_set(payload.get("vwap_inputs") or payload.get("vwapInputs"), default_source="one_minute_candles", default_fields=("high", "low", "close", "volume"), default_lookback=len(candles)),
        "volume_inputs": _input_set(payload.get("volume_inputs") or payload.get("volumeInputs"), default_source="one_minute_candles", default_fields=("volume",), default_lookback=len(candles)),
        "atr_inputs": _input_set(payload.get("atr_inputs") or payload.get("atrInputs"), default_source="one_minute_candles", default_fields=("high", "low", "close"), default_lookback=min(len(candles), 15)),
        "data_freshness_seconds": _data_freshness_seconds(payload, timestamp, candles),
        "data_manifest_hash": "",
        "explanation": "Weighted Voting market snapshot built from shared raw market facts only.",
    }
    if not snapshot_values["data_manifest_hash"]:
        snapshot_values["data_manifest_hash"] = _manifest_hash(snapshot_values)
    return WeightedVotingMarketSnapshot(**snapshot_values)


def payload_contains_foreign_algorithm_fields(payload: dict[str, Any]) -> bool:
    return any(field in payload for field in FORBIDDEN_FOREIGN_ALGORITHM_FIELDS)


def _candles_from_rows(rows: Any) -> tuple[WeightedCandle, ...]:
    if not rows:
        return ()
    return tuple(
        WeightedCandle(
            timestamp=_parse_datetime(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for row in rows
    )


def _reference_levels(value: Any) -> WeightedReferenceLevels | None:
    data = _object_value(value)
    if not data:
        return None
    return WeightedReferenceLevels(
        high=_optional_float(data.get("high")),
        low=_optional_float(data.get("low")),
        close=_optional_float(data.get("close")),
    )


def _derived_opening_range(candles: tuple[WeightedCandle, ...]) -> WeightedReferenceLevels | None:
    if len(candles) < 3:
        return None
    opening = candles[: min(15, len(candles))]
    return WeightedReferenceLevels(high=max(candle.high for candle in opening), low=min(candle.low for candle in opening), close=opening[-1].close)


def _input_set(value: Any, *, default_source: str, default_fields: tuple[str, ...], default_lookback: int) -> WeightedSnapshotInputSet:
    data = _object_value(value)
    if not data:
        return WeightedSnapshotInputSet(source=default_source, fields=default_fields, lookback=default_lookback)
    return WeightedSnapshotInputSet(
        source=str(data.get("source") or default_source),
        fields=tuple(str(field) for field in data.get("fields", default_fields)),
        lookback=int(data["lookback"]) if data.get("lookback") is not None else default_lookback,
        values=tuple(float(item) for item in data.get("values", ())),
        metadata=tuple(str(item) for item in data.get("metadata", ())),
    )


def _data_freshness_seconds(payload: dict[str, Any], timestamp: datetime, candles: tuple[WeightedCandle, ...]) -> float:
    explicit = payload.get("data_freshness_seconds", payload.get("dataFreshnessSeconds"))
    if explicit is not None:
        return max(0.0, float(explicit))
    return max(0.0, (timestamp - candles[-1].timestamp).total_seconds())


def _session_date(payload: dict[str, Any], session_info: dict[str, Any], timestamp: datetime) -> str:
    value = payload.get("session_date") or payload.get("sessionDate") or session_info.get("date") or session_info.get("session_date")
    return str(value) if value else timestamp.date().isoformat()


def _session_phase(payload: dict[str, Any], session_info: dict[str, Any]) -> WeightedSessionPhase:
    value = payload.get("session_phase") or payload.get("sessionPhase") or session_info.get("phase") or session_info.get("session_phase")
    if not value:
        return WeightedSessionPhase.UNKNOWN
    try:
        return WeightedSessionPhase(str(value))
    except ValueError:
        return WeightedSessionPhase.UNKNOWN


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    normalized = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _manifest_hash(snapshot_values: dict[str, Any]) -> str:
    sanitized = {
        key: _json_ready(value)
        for key, value in snapshot_values.items()
        if key not in {"data_manifest_hash", "explanation"}
    }
    return hashlib.sha256(json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items())}
    return value


__all__ = [
    "FORBIDDEN_FOREIGN_ALGORITHM_FIELDS",
    "WEIGHTED_VOTING_MARKET_SNAPSHOT_ADAPTER_VERSION",
    "WeightedCandle",
    "WeightedMarketSnapshot",
    "WeightedVotingCandle",
    "WeightedVotingMarketSnapshot",
    "build_weighted_voting_market_snapshot",
    "payload_contains_foreign_algorithm_fields",
]
