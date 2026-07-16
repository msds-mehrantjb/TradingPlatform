"""Replay-stage snapshot bridge for Voting Ensemble meta-model training."""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


VOTING_ENSEMBLE_REPLAY_LABEL_VERSION = "candidate_triple_barrier_v1"
VOTING_ENSEMBLE_REPLAY_SNAPSHOT_SOURCE_VERSION = "voting_ensemble_replay_stage_result_v1"
VOTING_ENSEMBLE_REPLAY_SNAPSHOT_BRIDGE_VERSION = "voting_ensemble_replay_snapshot_bridge_v1"


def stage_result_to_v2_training_row(
    stage_result: dict[str, Any],
    *,
    timeframe: str,
    data_quality: dict[str, Any] | None = None,
    label_version: str = VOTING_ENSEMBLE_REPLAY_LABEL_VERSION,
    market_data_feed: str = "alpaca_paper",
) -> dict[str, Any] | None:
    """Convert one filled replay candidate into a DecisionSnapshotV2-compatible label row."""

    stages = stage_result.get("stages") if isinstance(stage_result.get("stages"), dict) else {}
    candidate = stage_result.get("candidate") or stages.get("candidateOrder")
    fill = stage_result.get("fill") or ((stages.get("execution") or {}).get("fill") if isinstance(stages.get("execution"), dict) else None)
    exit_result = stage_result.get("exit") or ((stages.get("execution") or {}).get("exit") if isinstance(stages.get("execution"), dict) else None)
    if not isinstance(candidate, dict) or not isinstance(fill, dict) or not isinstance(exit_result, dict):
        return None
    if not fill.get("filledAt") or not exit_result.get("exitAt") or _number(fill.get("filledQuantity")) <= 0:
        return None

    decision_at = _parse_utc(str(stage_result.get("decisionTimestampUtc") or stages.get("decisionTimestampUtc") or fill.get("submittedAt")))
    label_start = _parse_utc(str(fill.get("filledAt")))
    label_end = _parse_utc(str(exit_result.get("exitAt")))
    session_date = str(candidate.get("sessionDate") or decision_at.date().isoformat())
    side_label = _side_to_label(candidate.get("side") or fill.get("side") or stage_result.get("finalSignal"))
    pnl = _number(exit_result.get("pnl"))
    success = pnl > 0
    family_scores = _signed_family_scores(stage_result.get("familyScores") or (stages.get("familyAwareEnsemble") or {}).get("familyScores") or {})
    final_signal = _signal_to_label(stage_result.get("finalSignal") or side_label)
    aggregation = _family_aggregation_features(family_scores)
    directional = stage_result.get("strategyOutputs") or stages.get("directionalStrategies") or []
    context = stage_result.get("contextSignals") or stages.get("contextSignals") or []
    source_quality = data_quality or {}
    training_ready = _data_quality_training_ready(source_quality)

    return {
        "snapshotSchemaVersion": "decision_snapshot_v2",
        "sourceSchemaVersion": VOTING_ENSEMBLE_REPLAY_SNAPSHOT_SOURCE_VERSION,
        "snapshotBridgeVersion": VOTING_ENSEMBLE_REPLAY_SNAPSHOT_BRIDGE_VERSION,
        "snapshotId": f"ve-replay-{timeframe}-{_safe_name(session_date)}-{int(decision_at.timestamp())}",
        "capturedAt": _iso(decision_at),
        "decisionTimestampUtc": _iso(decision_at),
        "labelStart": _iso(label_start),
        "labelEnd": _iso(label_end),
        "entryTimestampUtc": _iso(label_start),
        "exitTimestampUtc": _iso(label_end),
        "sessionDate": session_date,
        "sessionDateNewYork": session_date,
        "symbol": str(stage_result.get("symbol") or candidate.get("symbol") or "SPY").upper(),
        "timeframe": timeframe,
        "labelVersion": label_version,
        "trainingLabel": side_label,
        "validationLabel": side_label,
        "costAdjustedTrainingLabel": 1 if success else 0,
        "strictOutcomeLabel": "success" if success else "failure",
        "successfulCandidate": success,
        "eligibleForTraining": training_ready,
        "trainingCompatibleWithV2": True,
        "algorithmVersion": "voting_ensemble_backend_v2_replay",
        "strategySchemaVersion": "voting_ensemble_strategy_signal_v2",
        "featureSchemaVersion": "candidate_meta_feature_schema_v1",
        "marketDataFeed": market_data_feed,
        "rawMarketReferences": {
            "provider": market_data_feed,
            "pointInTimeReplay": True,
            "dataQuality": source_quality,
        },
        "forecastFeature": {"status": "missing_approved_forecast_model"},
        "regimeState": {"label": _regime_label(stage_result)},
        "familyScores": {"meta": aggregation},
        "metaModelFeatures": {
            "familyAggregation": aggregation,
            "familyScores": _family_score_vectors(family_scores),
            "context": _context_features(context),
            "candidate": {
                "baseScore": _number(stage_result.get("baseScore")),
                "contextAdjustedScore": _number(stage_result.get("contextAdjustedScore")),
                "confidence": abs(_number(stage_result.get("contextAdjustedScore") or stage_result.get("baseScore"))),
            },
            "replay": {
                "timeframe": timeframe,
                "positionActive": bool((stages.get("safetyAndPosition") or {}).get("positionActive")),
                "eligibleForNewEntry": bool((stages.get("safetyAndPosition") or {}).get("eligibleForNewEntry")),
            },
        },
        "strategyOutputs": {
            "voting": list(directional) if isinstance(directional, list) else [],
            "confidence": _confidence_outputs(directional),
            "weighted": _weighted_outputs(directional),
        },
        "contextOutputs": list(context) if isinstance(context, list) else [],
        "finalDecision": {
            "voting": {"signal": final_signal},
            "weighted": {"signal": final_signal},
            "confidence": {"signal": final_signal},
            "regime": {"signal": final_signal},
            "meta": {"signal": final_signal},
        },
        "barriers": {
            "targetPrice": _number(candidate.get("targetPrice")),
            "stopPrice": _number(candidate.get("stopPrice")),
            "targetDistance": abs(_number(candidate.get("targetPrice")) - _number(candidate.get("entryPrice"))),
            "stopDistance": abs(_number(candidate.get("entryPrice")) - _number(candidate.get("stopPrice"))),
            "maximumHoldingMinutes": _number(candidate.get("maximumHoldingMinutes")),
        },
        "entry": {
            "plannedEntryPrice": _number(candidate.get("entryPrice")),
            "fillPrice": _number(fill.get("averagePrice")),
            "filledQuantity": _number(fill.get("filledQuantity")),
            "spread": _number((fill.get("costs") or {}).get("spread")),
            "fees": _number((fill.get("costs") or {}).get("fees")),
        },
        "outcome": {
            "closedAt": _iso(label_end),
            "exitReason": exit_result.get("exitReason"),
            "exitPrice": _number(exit_result.get("exitPrice")),
            "pnl": pnl,
            "success": success,
            "costAdjustedTrainingLabel": 1 if success else 0,
        },
        "replayStageResult": {
            "schemaVersion": stage_result.get("schemaVersion"),
            "reasonCodes": stage_result.get("reasonCodes") or [],
            "executionReasonCodes": ((stages.get("execution") or {}).get("reasonCodes") if isinstance(stages.get("execution"), dict) else []) or [],
        },
    }


def write_voting_ensemble_replay_snapshot_labels(
    *,
    stage_results: list[dict[str, Any]],
    output_root: Path,
    symbol: str,
    timeframe: str,
    data_quality: dict[str, Any] | None = None,
    label_version: str = VOTING_ENSEMBLE_REPLAY_LABEL_VERSION,
    market_data_feed: str = "alpaca_paper",
) -> dict[str, Any]:
    rows = [
        row
        for row in (
            stage_result_to_v2_training_row(
                stage_result,
                timeframe=timeframe,
                data_quality=data_quality,
                label_version=label_version,
                market_data_feed=market_data_feed,
            )
            for stage_result in stage_results
        )
        if row is not None
    ]
    return write_v2_label_rows(rows=rows, output_root=output_root, symbol=symbol, source=f"stage_results:{timeframe}")


def merge_voting_ensemble_replay_snapshot_labels(*, source_roots: list[Path], output_root: Path, symbol: str) -> dict[str, Any]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    safe_symbol = _safe_name(symbol.upper())
    for root in source_roots:
        for path in sorted(root.glob(f"*/{safe_symbol}_decision_labels.jsonl")):
            for row in _read_jsonl(path):
                rows_by_id[str(row.get("snapshotId") or f"{path}:{len(rows_by_id)}")] = row
    if output_root.exists():
        shutil.rmtree(output_root)
    return write_v2_label_rows(rows=sorted(rows_by_id.values(), key=lambda row: str(row.get("decisionTimestampUtc") or "")), output_root=output_root, symbol=symbol, source="merged_replay_snapshots")


def write_v2_label_rows(*, rows: list[dict[str, Any]], output_root: Path, symbol: str, source: str) -> dict[str, Any]:
    safe_symbol = _safe_name(symbol.upper())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("sessionDate") or "unknown")].append(row)

    paths: list[str] = []
    for session_date, session_rows in sorted(grouped.items()):
        path = output_root / _safe_name(session_date) / f"{safe_symbol}_decision_labels.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            for row in sorted(session_rows, key=lambda item: str(item.get("decisionTimestampUtc") or "")):
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")
        paths.append(str(path))

    manifest = {
        "version": VOTING_ENSEMBLE_REPLAY_SNAPSHOT_BRIDGE_VERSION,
        "source": source,
        "symbol": symbol.upper(),
        "snapshotRoot": str(output_root),
        "rowCount": len(rows),
        "eligibleRowCount": sum(1 for row in rows if row.get("eligibleForTraining") is True),
        "sessionCount": len(grouped),
        "rowsJsonl": paths,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def _data_quality_training_ready(data_quality: dict[str, Any]) -> bool:
    if not data_quality:
        return False
    return bool(
        data_quality.get("usesActual5m")
        and data_quality.get("usesActual15m")
        and data_quality.get("usesActualQqqIwm")
        and not data_quality.get("usesSyntheticQqqIwm")
        and int(data_quality.get("breadthComponentCount") or 0) > 0
    )


def _family_aggregation_features(family_scores: dict[str, float]) -> dict[str, float]:
    features: dict[str, float] = {}
    for family, score in family_scores.items():
        normalized = _number(score)
        features[f"{family}_buy_score"] = max(0.0, normalized)
        features[f"{family}_sell_score"] = max(0.0, -normalized)
        features[f"{family}_hold_score"] = max(0.0, 1.0 - abs(normalized))
    features["regime_score"] = sum(family_scores.values()) / len(family_scores) if family_scores else 0.0
    return features


def _family_score_vectors(family_scores: dict[str, float]) -> dict[str, dict[str, float]]:
    return {
        family: {"buy": max(0.0, score), "sell": max(0.0, -score), "hold": max(0.0, 1.0 - abs(score))}
        for family, score in family_scores.items()
    }


def _signed_family_scores(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _number(score) for key, score in value.items()}


def _context_features(context: Any) -> dict[str, float]:
    if not isinstance(context, list):
        return {}
    features: dict[str, float] = {}
    for item in context:
        if not isinstance(item, dict):
            continue
        name = _safe_name(str(item.get("strategy") or "context")).lower()
        direction = _number(item.get("direction"))
        confidence = _number(item.get("confidence"))
        features[f"{name}_direction"] = direction
        features[f"{name}_confidence"] = confidence
    return features


def _confidence_outputs(outputs: Any) -> list[dict[str, Any]]:
    if not isinstance(outputs, list):
        return []
    rows = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        direction = _number(output.get("direction"))
        confidence = _number(output.get("confidence"))
        reliability = _number(output.get("reliability") or 1.0)
        regime_fit = _number(output.get("regimeFit") or output.get("regime_fit") or 1.0)
        rows.append({**output, "contribution": direction * confidence * reliability * regime_fit})
    return rows


def _weighted_outputs(outputs: Any) -> list[dict[str, Any]]:
    if not isinstance(outputs, list):
        return []
    rows = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        signal = _signal_to_label(output.get("signal"))
        confidence = _number(output.get("confidence"))
        rows.append(
            {
                **output,
                "pBuy": confidence if signal == "BUY" else 0.0,
                "pSell": confidence if signal == "SELL" else 0.0,
                "pHold": confidence if signal == "HOLD" else 0.0,
                "baseWeight": _number(output.get("reliability") or 1.0),
            }
        )
    return rows


def _regime_label(stage_result: dict[str, Any]) -> str:
    family_scores = _signed_family_scores(stage_result.get("familyScores"))
    base_score = _number(stage_result.get("baseScore"))
    if abs(base_score) >= 0.45 or any(abs(score) >= 0.45 for score in family_scores.values()):
        return "strong_trend"
    if abs(base_score) >= 0.15:
        return "weak_trend"
    return "range"


def _side_to_label(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"BUY", "LONG"}:
        return "BUY"
    if text in {"SELL", "SHORT"}:
        return "SELL"
    return _signal_to_label(value)


def _signal_to_label(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULLISH", "LONG"}:
        return "BUY"
    if text in {"SELL", "BEARISH", "SHORT"}:
        return "SELL"
    return "HOLD"


def _number(value: Any) -> float:
    try:
        if value is True:
            return 1.0
        if value is False or value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "-", value).strip("-") or "unknown"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
