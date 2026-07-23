"""Passive out-of-sample golden-regime occupancy validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from backend.app.algorithms.regime.contracts import RegimeClassification
from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION = "regime_golden_occupancy_validation_v1"
REGIME_GOLDEN_OCCUPANCY_VALIDATION_TYPE = "golden_regime_out_of_sample_occupancy"
OUT_OF_SAMPLE_PARTITIONS = frozenset({"final_holdout", "out_of_sample", "paper_shadow", "live_paper_shadow"})


@dataclass(frozen=True)
class GoldenRegimeOccupancyBound:
    regime: str
    minimum_rate: float
    maximum_rate: float
    minimum_count: int = 1


DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS: tuple[GoldenRegimeOccupancyBound, ...] = (
    GoldenRegimeOccupancyBound("strong_uptrend", 0.02, 0.60),
    GoldenRegimeOccupancyBound("range_bound", 0.01, 0.50),
    GoldenRegimeOccupancyBound("low_volatility_quiet", 0.01, 0.40),
    GoldenRegimeOccupancyBound("intraday_expansion", 0.005, 0.30),
    GoldenRegimeOccupancyBound("opening_breakout", 0.0025, 0.20),
    GoldenRegimeOccupancyBound("failed_breakout_reversal", 0.0025, 0.20),
    GoldenRegimeOccupancyBound("liquidity_stress", 0.0025, 0.25),
    GoldenRegimeOccupancyBound("event_risk", 0.001, 0.20),
)


def validate_golden_regime_occupancy(
    classifications: Iterable[RegimeClassification | Mapping[str, Any]],
    *,
    partition: str,
    expected_bounds: Iterable[GoldenRegimeOccupancyBound | Mapping[str, Any]] = DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS,
    allow_inactive: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Validate that golden regimes appear at reasonable rates in holdout data.

    The result remains inactive by default. Passing ``allow_inactive=True`` is
    reserved for explicit paper/live validation wiring.
    """

    records = [_classification_record(item) for item in classifications]
    total = len(records)
    counts: dict[str, int] = {}
    timestamps = [record["timestamp"] for record in records if record.get("timestamp")]
    for record in records:
        regime = record.get("rawRegime")
        if regime:
            counts[regime] = counts.get(regime, 0) + 1

    normalized_partition = str(partition or "").lower()
    out_of_sample = normalized_partition in OUT_OF_SAMPLE_PARTITIONS
    chronological = _strictly_increasing_timestamps(timestamps)
    bounds = tuple(_coerce_bound(bound) for bound in expected_bounds)
    occupancy = {
        bound.regime: _regime_occupancy_report(bound, counts.get(bound.regime, 0), total)
        for bound in bounds
    }
    diagnostic_passed = (
        total > 0
        and out_of_sample
        and chronological
        and all(item["passed"] for item in occupancy.values())
    )
    reason_codes = _reason_codes(
        total=total,
        out_of_sample=out_of_sample,
        chronological=chronological,
        occupancy=occupancy,
    )
    validation_status = "pass" if diagnostic_passed else "fail"
    if not allow_inactive:
        validation_status = INACTIVE_UNTIL_LIVE_PAPER_TRADING

    return {
        "algorithmId": "regime",
        "validationType": REGIME_GOLDEN_OCCUPANCY_VALIDATION_TYPE,
        "validationVersion": REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION,
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "validationStatus": validation_status,
        "diagnosticPassed": diagnostic_passed,
        "validationAppliedToPromotion": bool(allow_inactive and diagnostic_passed),
        "partition": normalized_partition,
        "outOfSample": out_of_sample,
        "chronological": chronological,
        "totalObservations": total,
        "distinctRegimesObserved": len(counts),
        "occupancy": occupancy,
        "missingGoldenRegimes": [regime for regime, item in occupancy.items() if item["count"] < item["minimumCount"]],
        "excessiveGoldenRegimes": [regime for regime, item in occupancy.items() if item["rate"] > item["maximumRate"]],
        "reasonCodes": reason_codes,
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
    }


def _classification_record(item: RegimeClassification | Mapping[str, Any]) -> dict[str, str | None]:
    if isinstance(item, RegimeClassification):
        return {"rawRegime": item.raw_regime, "timestamp": item.timestamp}
    source = item
    raw_classification = source.get("raw_classification") or source.get("rawClassification")
    if isinstance(raw_classification, Mapping):
        source = raw_classification
    return {
        "rawRegime": _text(source.get("raw_regime") or source.get("rawRegime") or source.get("regime")),
        "timestamp": _text(source.get("timestamp") or source.get("eventTimestamp") or source.get("decisionTimestamp")),
    }


def _regime_occupancy_report(
    bound: GoldenRegimeOccupancyBound,
    count: int,
    total: int,
) -> dict[str, Any]:
    rate = (count / total) if total > 0 else 0.0
    reason_codes: list[str] = []
    if count < bound.minimum_count or rate < bound.minimum_rate:
        reason_codes.append("regime.occupancy.golden_regime_underrepresented")
    if rate > bound.maximum_rate:
        reason_codes.append("regime.occupancy.golden_regime_overrepresented")
    return {
        "regime": bound.regime,
        "count": count,
        "rate": rate,
        "minimumRate": bound.minimum_rate,
        "maximumRate": bound.maximum_rate,
        "minimumCount": bound.minimum_count,
        "passed": not reason_codes,
        "reasonCodes": reason_codes,
    }


def _reason_codes(
    *,
    total: int,
    out_of_sample: bool,
    chronological: bool,
    occupancy: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if total <= 0:
        reasons.append("regime.occupancy.empty_validation_set")
    if not out_of_sample:
        reasons.append("regime.occupancy.out_of_sample_partition_required")
    if not chronological:
        reasons.append("regime.occupancy.timestamps_not_strictly_increasing")
    for regime, item in occupancy.items():
        for reason in item.get("reasonCodes") or ():
            reasons.append(f"{reason}:{regime}")
    return tuple(dict.fromkeys(reasons))


def _strictly_increasing_timestamps(timestamps: list[str | None]) -> bool:
    parsed = [_parse_time(value) for value in timestamps if value]
    if not parsed:
        return True
    return all(left < right for left, right in zip(parsed, parsed[1:]))


def _coerce_bound(bound: GoldenRegimeOccupancyBound | Mapping[str, Any]) -> GoldenRegimeOccupancyBound:
    if isinstance(bound, GoldenRegimeOccupancyBound):
        return bound
    return GoldenRegimeOccupancyBound(
        regime=str(bound.get("regime") or bound.get("rawRegime") or ""),
        minimum_rate=float(bound.get("minimumRate") or bound.get("minimum_rate") or 0.0),
        maximum_rate=float(bound.get("maximumRate") or bound.get("maximum_rate") or 1.0),
        minimum_count=int(bound.get("minimumCount") or bound.get("minimum_count") or 1),
    )


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
