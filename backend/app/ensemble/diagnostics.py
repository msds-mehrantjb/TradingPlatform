from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from math import sqrt
from typing import Any

from pydantic import Field, field_validator

from backend.app.domain.models import Direction, DomainModel, Signal, StrategyFamily, _require_utc


class StrategySignalObservation(DomainModel):
    strategyId: str = Field(min_length=1)
    decisionKey: str = Field(min_length=1)
    signal: Signal


class StrategyCorrelationDiagnostic(DomainModel):
    version: str
    strategyA: str
    strategyB: str
    observations: int
    matchingSignals: int
    simultaneousEntries: int
    entryOverlapRate: float
    directionCorrelation: float
    identicalSignalRate: float
    explanation: str


class HistoricalDecisionTimeStrategyOutput(DomainModel):
    decisionKey: str = Field(min_length=1)
    decisionTimestamp: datetime
    walkForwardFold: str = Field(min_length=1)
    isOutOfSample: bool
    strategyId: str = Field(min_length=1)
    strategyName: str = Field(default="", min_length=0)
    family: StrategyFamily
    signal: Signal
    direction: Direction
    eligible: bool
    setupId: str | None = None
    outcomeR: float | None = None

    @field_validator("decisionTimestamp")
    @classmethod
    def decision_timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PairwiseDiversityDiagnostic(DomainModel):
    strategyA: str
    strategyB: str
    familyA: StrategyFamily
    familyB: StrategyFamily
    observations: int
    signalCorrelation: float
    directionalAgreementRate: float
    errorCorrelation: float
    tradeOverlapRate: float
    setupOverlapRate: float
    familyOverlap: bool
    nearlyIdentical: bool
    inclusionTestingOnly: bool
    explanation: str


class InclusionPerformanceDiagnostic(DomainModel):
    subjectId: str
    subjectType: str
    family: StrategyFamily | None = None
    outOfSampleTrades: int
    addOneExpectancy: float
    addOneMaxDrawdown: float
    leaveOneOutExpectancy: float
    leaveOneOutMaxDrawdown: float
    ensembleExpectancyWithSubject: float
    ensembleExpectancyWithoutSubject: float
    incrementalExpectancy: float
    incrementalDrawdownEffect: float
    explanation: str


class StrategyDiversityDiagnosticsReport(DomainModel):
    version: str
    generatedAt: datetime
    outOfSampleOnly: bool
    walkForwardFolds: list[str]
    strategyDiagnostics: list[InclusionPerformanceDiagnostic]
    familyDiagnostics: list[InclusionPerformanceDiagnostic]
    pairwiseDiagnostics: list[PairwiseDiversityDiagnostic]
    pairwiseCorrelationMatrix: dict[str, dict[str, float]]
    familyCorrelationMatrix: dict[str, dict[str, float]]
    nearlyIdenticalPairs: list[PairwiseDiversityDiagnostic]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def strategy_signal_correlation(
    observations: list[StrategySignalObservation] | list[dict[str, Any]],
    *,
    strategy_a: str,
    strategy_b: str,
) -> StrategyCorrelationDiagnostic:
    normalized = [
        observation if isinstance(observation, StrategySignalObservation) else StrategySignalObservation(**observation)
        for observation in observations
    ]
    by_key: dict[str, dict[str, Signal]] = defaultdict(dict)
    for observation in normalized:
        if observation.strategyId in {strategy_a, strategy_b}:
            by_key[observation.decisionKey][observation.strategyId] = Signal(observation.signal)

    paired = [
        (rows[strategy_a], rows[strategy_b])
        for rows in by_key.values()
        if strategy_a in rows and strategy_b in rows
    ]
    matching = sum(1 for left, right in paired if left == right)
    simultaneous_entries = sum(1 for left, right in paired if left != Signal.HOLD and right != Signal.HOLD)
    any_entries = sum(1 for left, right in paired if left != Signal.HOLD or right != Signal.HOLD)
    correlation = _pearson([_direction(left) for left, _ in paired], [_direction(right) for _, right in paired])
    overlap_rate = simultaneous_entries / any_entries if any_entries else 0.0
    identical_rate = matching / len(paired) if paired else 0.0

    return StrategyCorrelationDiagnostic(
        version="strategy_signal_correlation_v1",
        strategyA=strategy_a,
        strategyB=strategy_b,
        observations=len(paired),
        matchingSignals=matching,
        simultaneousEntries=simultaneous_entries,
        entryOverlapRate=round(overlap_rate, 4),
        directionCorrelation=round(correlation, 4),
        identicalSignalRate=round(identical_rate, 4),
        explanation=(
            f"{strategy_a} and {strategy_b} matched on {matching} of {len(paired)} paired observations; "
            f"entry overlap rate {overlap_rate:.2f}."
        ),
    )


def strategy_diversity_diagnostics(
    observations: list[HistoricalDecisionTimeStrategyOutput] | list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> StrategyDiversityDiagnosticsReport:
    normalized = [
        observation if isinstance(observation, HistoricalDecisionTimeStrategyOutput) else HistoricalDecisionTimeStrategyOutput(**observation)
        for observation in observations
    ]
    out_of_sample = [row for row in normalized if row.isOutOfSample]
    if not out_of_sample:
        raise ValueError("strategy diversity diagnostics require out-of-sample walk-forward observations")

    strategy_ids = sorted({row.strategyId for row in out_of_sample})
    families = sorted({StrategyFamily(row.family) for row in out_of_sample}, key=lambda item: item.value)
    pairwise = [_pairwise_diagnostic(out_of_sample, left, right) for left, right in combinations(strategy_ids, 2)]
    pairwise_matrix = _strategy_correlation_matrix(out_of_sample, strategy_ids)
    family_matrix = _family_correlation_matrix(out_of_sample, families)
    baseline = _performance(out_of_sample)
    strategy_diagnostics = [
        _inclusion_diagnostic(
            out_of_sample,
            subject_id=strategy_id,
            subject_type="strategy",
            include=lambda row, strategy_id=strategy_id: row.strategyId == strategy_id,
            exclude=lambda row, strategy_id=strategy_id: row.strategyId != strategy_id,
            baseline=baseline,
            family=_family_for_strategy(out_of_sample, strategy_id),
        )
        for strategy_id in strategy_ids
    ]
    family_diagnostics = [
        _inclusion_diagnostic(
            out_of_sample,
            subject_id=family.value,
            subject_type="family",
            include=lambda row, family=family: row.family == family.value,
            exclude=lambda row, family=family: row.family != family.value,
            baseline=baseline,
            family=family,
        )
        for family in families
    ]
    nearly_identical = [row for row in pairwise if row.nearlyIdentical]
    generated = generated_at or datetime.now(UTC)
    return StrategyDiversityDiagnosticsReport(
        version="strategy_diversity_diagnostics_v1",
        generatedAt=generated,
        outOfSampleOnly=True,
        walkForwardFolds=sorted({row.walkForwardFold for row in out_of_sample}),
        strategyDiagnostics=strategy_diagnostics,
        familyDiagnostics=family_diagnostics,
        pairwiseDiagnostics=pairwise,
        pairwiseCorrelationMatrix=pairwise_matrix,
        familyCorrelationMatrix=family_matrix,
        nearlyIdenticalPairs=nearly_identical,
        explanation=(
            "Diagnostics are computed from out-of-sample walk-forward decision-time outputs only. "
            "Near-identical signals are reported for inclusion testing; no strategy is automatically removed."
        ),
    )


def _direction(signal: Signal) -> int:
    if signal == Signal.BUY:
        return 1
    if signal == Signal.SELL:
        return -1
    return 0


def _row_direction(row: HistoricalDecisionTimeStrategyOutput) -> int:
    if not row.eligible:
        return 0
    return int(row.direction)


def _entry(row: HistoricalDecisionTimeStrategyOutput) -> bool:
    return bool(row.eligible and row.signal != Signal.HOLD.value and row.direction != Direction.FLAT.value)


def _error(row: HistoricalDecisionTimeStrategyOutput) -> int | None:
    if not _entry(row) or row.outcomeR is None:
        return None
    return 1 if row.outcomeR < 0 else 0


def _pairwise_diagnostic(
    rows: list[HistoricalDecisionTimeStrategyOutput],
    strategy_a: str,
    strategy_b: str,
) -> PairwiseDiversityDiagnostic:
    by_key: dict[str, dict[str, HistoricalDecisionTimeStrategyOutput]] = defaultdict(dict)
    for row in rows:
        if row.strategyId in {strategy_a, strategy_b}:
            by_key[row.decisionKey][row.strategyId] = row
    paired = [(items[strategy_a], items[strategy_b]) for items in by_key.values() if strategy_a in items and strategy_b in items]
    left_directions = [_row_direction(left) for left, _ in paired]
    right_directions = [_row_direction(right) for _, right in paired]
    signal_correlation = _pearson(left_directions, right_directions)
    directional_agreement = sum(1 for left, right in zip(left_directions, right_directions) if left == right) / len(paired) if paired else 0.0
    simultaneous_entries = sum(1 for left, right in paired if _entry(left) and _entry(right))
    any_entries = sum(1 for left, right in paired if _entry(left) or _entry(right))
    trade_overlap = simultaneous_entries / any_entries if any_entries else 0.0
    both_setup = [(left, right) for left, right in paired if left.setupId and right.setupId]
    setup_overlap = sum(1 for left, right in both_setup if left.setupId == right.setupId) / len(both_setup) if both_setup else 0.0
    error_pairs = [(_error(left), _error(right)) for left, right in paired if _error(left) is not None and _error(right) is not None]
    error_correlation = _pearson([int(left) for left, _ in error_pairs], [int(right) for _, right in error_pairs]) if error_pairs else 0.0
    family_a = _family_for_strategy(rows, strategy_a)
    family_b = _family_for_strategy(rows, strategy_b)
    nearly_identical = bool(
        len(paired) >= 3
        and signal_correlation >= 0.98
        and directional_agreement >= 0.95
        and (trade_overlap >= 0.90 or setup_overlap >= 0.90)
    )
    return PairwiseDiversityDiagnostic(
        strategyA=strategy_a,
        strategyB=strategy_b,
        familyA=family_a,
        familyB=family_b,
        observations=len(paired),
        signalCorrelation=round(signal_correlation, 4),
        directionalAgreementRate=round(directional_agreement, 4),
        errorCorrelation=round(error_correlation, 4),
        tradeOverlapRate=round(trade_overlap, 4),
        setupOverlapRate=round(setup_overlap, 4),
        familyOverlap=family_a == family_b,
        nearlyIdentical=nearly_identical,
        inclusionTestingOnly=True,
        explanation=(
            f"{strategy_a} vs {strategy_b}: signal correlation {signal_correlation:.2f}, "
            f"trade overlap {trade_overlap:.2f}, setup overlap {setup_overlap:.2f}. "
            "This diagnostic reports similarity for inclusion testing and does not automatically remove either strategy."
        ),
    )


def _strategy_correlation_matrix(rows: list[HistoricalDecisionTimeStrategyOutput], strategy_ids: list[str]) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {strategy_id: {} for strategy_id in strategy_ids}
    by_key: dict[str, dict[str, HistoricalDecisionTimeStrategyOutput]] = defaultdict(dict)
    for row in rows:
        by_key[row.decisionKey][row.strategyId] = row
    for left in strategy_ids:
        for right in strategy_ids:
            paired = [(items[left], items[right]) for items in by_key.values() if left in items and right in items]
            matrix[left][right] = round(_pearson([_row_direction(a) for a, _ in paired], [_row_direction(b) for _, b in paired]), 4)
    return matrix


def _family_correlation_matrix(rows: list[HistoricalDecisionTimeStrategyOutput], families: list[StrategyFamily]) -> dict[str, dict[str, float]]:
    by_key: dict[str, dict[StrategyFamily, float]] = defaultdict(dict)
    for key, family_rows in _rows_by_decision_family(rows).items():
        for family, grouped in family_rows.items():
            by_key[key][family] = sum(_row_direction(row) for row in grouped) / len(grouped)
    matrix: dict[str, dict[str, float]] = {family.value: {} for family in families}
    for left in families:
        for right in families:
            paired = [(items[left], items[right]) for items in by_key.values() if left in items and right in items]
            matrix[left.value][right.value] = round(_pearson([a for a, _ in paired], [b for _, b in paired]), 4)
    return matrix


def _rows_by_decision_family(
    rows: list[HistoricalDecisionTimeStrategyOutput],
) -> dict[str, dict[StrategyFamily, list[HistoricalDecisionTimeStrategyOutput]]]:
    result: dict[str, dict[StrategyFamily, list[HistoricalDecisionTimeStrategyOutput]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        result[row.decisionKey][StrategyFamily(row.family)].append(row)
    return result


def _performance(rows: list[HistoricalDecisionTimeStrategyOutput]) -> dict[str, float | int]:
    trade_rows = sorted((row for row in rows if _entry(row) and row.outcomeR is not None), key=lambda row: row.decisionTimestamp)
    outcomes = [float(row.outcomeR) for row in trade_rows]
    expectancy = sum(outcomes) / len(outcomes) if outcomes else 0.0
    return {
        "trades": len(outcomes),
        "expectancy": expectancy,
        "maxDrawdown": _max_drawdown(outcomes),
    }


def _inclusion_diagnostic(
    rows: list[HistoricalDecisionTimeStrategyOutput],
    *,
    subject_id: str,
    subject_type: str,
    include: Any,
    exclude: Any,
    baseline: dict[str, float | int],
    family: StrategyFamily | None,
) -> InclusionPerformanceDiagnostic:
    add_one = _performance([row for row in rows if include(row)])
    leave_one_out = _performance([row for row in rows if exclude(row)])
    baseline_expectancy = float(baseline["expectancy"])
    leave_out_expectancy = float(leave_one_out["expectancy"])
    baseline_drawdown = float(baseline["maxDrawdown"])
    leave_out_drawdown = float(leave_one_out["maxDrawdown"])
    return InclusionPerformanceDiagnostic(
        subjectId=subject_id,
        subjectType=subject_type,
        family=family,
        outOfSampleTrades=int(add_one["trades"]),
        addOneExpectancy=round(float(add_one["expectancy"]), 4),
        addOneMaxDrawdown=round(float(add_one["maxDrawdown"]), 4),
        leaveOneOutExpectancy=round(leave_out_expectancy, 4),
        leaveOneOutMaxDrawdown=round(leave_out_drawdown, 4),
        ensembleExpectancyWithSubject=round(baseline_expectancy, 4),
        ensembleExpectancyWithoutSubject=round(leave_out_expectancy, 4),
        incrementalExpectancy=round(baseline_expectancy - leave_out_expectancy, 4),
        incrementalDrawdownEffect=round(baseline_drawdown - leave_out_drawdown, 4),
        explanation=(
            f"{subject_type} {subject_id}: add-one expectancy {float(add_one['expectancy']):.2f}; "
            f"ensemble expectancy with subject {baseline_expectancy:.2f}, without subject {leave_out_expectancy:.2f}."
        ),
    )


def _family_for_strategy(rows: list[HistoricalDecisionTimeStrategyOutput], strategy_id: str) -> StrategyFamily:
    for row in rows:
        if row.strategyId == strategy_id:
            return StrategyFamily(row.family)
    raise ValueError(f"unknown strategy id {strategy_id}")


def _max_drawdown(outcomes: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for outcome in outcomes:
        equity += outcome
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _pearson(left: list[int], right: list[int]) -> float:
    if len(left) < 2 or len(left) != len(right):
        return 0.0
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_variance = sum((a - mean_left) ** 2 for a in left)
    right_variance = sum((b - mean_right) ** 2 for b in right)
    denominator = sqrt(left_variance * right_variance)
    return numerator / denominator if denominator else 0.0
