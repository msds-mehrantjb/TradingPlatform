"""Family-aware deterministic aggregation for Meta-Strategy candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.app.algorithms.meta_strategy.contracts import DeterministicCandidate, FamilyScore
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.strategy_registry import MetaStrategyRegistryEntry
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult


Direction = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class FamilyAggregationConfig:
    strategy_contribution_cap: float = 0.35
    family_contribution_cap: float = 0.60
    correlation_group_cap: float = 0.40
    minimum_active_strategies: int = 2
    minimum_independent_families: int = 2
    maximum_abstention_rate: float = 0.75
    minimum_conflict_edge: float = 0.05
    tie_tolerance: float = 1e-9


@dataclass(frozen=True)
class StrategyContribution:
    strategy_id: str
    family: str
    signal: Direction
    confidence: float
    eligible: bool = True
    weight: float = 1.0
    canonical_influence_id: str | None = None
    correlation_key: str | None = None


@dataclass(frozen=True)
class FamilyContribution:
    family: str
    buy_score: float
    sell_score: float
    hold_score: float
    active_strategy_count: int
    capped: bool


@dataclass(frozen=True)
class FamilyAggregationResult:
    signal: Direction
    eligible: bool
    confidence: float
    buy_score: float
    sell_score: float
    hold_score: float
    active_strategy_count: int
    active_family_count: int
    abstention_rate: float
    family_scores: tuple[FamilyContribution, ...]
    reason_codes: tuple[str, ...]

    def to_deterministic_candidate(
        self,
        *,
        algorithm_version: Literal["meta_strategy_algorithm_v1"],
        configuration_version: Literal["meta_strategy_config_v1"],
        strategy_catalog_version: Literal["meta_strategy_strategy_catalog_v1"],
        decision_id: str,
        snapshot_id: str,
        timestamp: datetime,
    ) -> DeterministicCandidate:
        return DeterministicCandidate(
            algorithm_id=ALGORITHM_ID,
            algorithm_version=algorithm_version,
            configuration_version=configuration_version,
            strategy_catalog_version=strategy_catalog_version,
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            signal=self.signal,
            confidence=self.confidence,
            eligible=self.eligible,
            family_scores=tuple(
                FamilyScore(
                    algorithm_id=ALGORITHM_ID,
                    algorithm_version=algorithm_version,
                    configuration_version=configuration_version,
                    strategy_catalog_version=strategy_catalog_version,
                    decision_id=decision_id,
                    snapshot_id=snapshot_id,
                    timestamp=timestamp,
                    family=score.family,
                    buy_score=score.buy_score,
                    sell_score=score.sell_score,
                    hold_score=score.hold_score,
                    confidence=max(score.buy_score, score.sell_score, score.hold_score),
                    reliability=1.0 if score.active_strategy_count else 0.0,
                )
                for score in self.family_scores
            ),
            reason_codes=self.reason_codes,
        )


def aggregate_family_scores(
    evaluations: tuple[SnapshotEvaluationResult | StrategyContribution, ...] | list[SnapshotEvaluationResult | StrategyContribution],
    *,
    registry_entries: tuple[MetaStrategyRegistryEntry, ...] | list[MetaStrategyRegistryEntry] = (),
    config: FamilyAggregationConfig | None = None,
) -> FamilyAggregationResult:
    settings = config or FamilyAggregationConfig()
    contributions = tuple(_as_contribution(item, registry_entries) for item in evaluations)
    total_count = len(contributions)
    if total_count == 0:
        return _hold_result("meta_strategy.aggregation.no_strategy_outputs")

    deduped, deduped_count = _dedupe_aliases(contributions)
    active = tuple(item for item in deduped if item.eligible and item.signal in {"BUY", "SELL"} and item.confidence > 0.0 and item.weight > 0.0)
    active_count = len(active)
    active_families = tuple(sorted({item.family for item in active}))
    abstention_rate = round(1.0 - active_count / max(1, total_count), 6)
    reason_codes: list[str] = []
    if deduped_count:
        reason_codes.append("meta_strategy.aggregation.alias_deduplicated")

    if active_count == 0:
        return _hold_result("meta_strategy.aggregation.no_active_directional_strategies", total_count=total_count, abstention_rate=1.0, extra_reasons=tuple(reason_codes))
    if active_count < settings.minimum_active_strategies:
        return _hold_result(
            "meta_strategy.aggregation.minimum_active_strategies",
            total_count=total_count,
            active_count=active_count,
            active_family_count=len(active_families),
            abstention_rate=abstention_rate,
            family_scores=_family_scores(active, settings),
            extra_reasons=tuple(reason_codes),
        )
    if len(active_families) < settings.minimum_independent_families:
        return _hold_result(
            "meta_strategy.aggregation.minimum_independent_families",
            total_count=total_count,
            active_count=active_count,
            active_family_count=len(active_families),
            abstention_rate=abstention_rate,
            family_scores=_family_scores(active, settings),
            extra_reasons=tuple(reason_codes),
        )
    if abstention_rate > settings.maximum_abstention_rate:
        return _hold_result(
            "meta_strategy.aggregation.maximum_abstention_rate",
            total_count=total_count,
            active_count=active_count,
            active_family_count=len(active_families),
            abstention_rate=abstention_rate,
            family_scores=_family_scores(active, settings),
            extra_reasons=tuple(reason_codes),
        )

    family_scores = _family_scores(active, settings)
    buy_score = round(sum(score.buy_score for score in family_scores), 6)
    sell_score = round(sum(score.sell_score for score in family_scores), 6)
    hold_score = round(max(0.0, abstention_rate), 6)
    if buy_score <= settings.tie_tolerance and sell_score <= settings.tie_tolerance:
        return _hold_result(
            "meta_strategy.aggregation.hold_fallback",
            total_count=total_count,
            active_count=active_count,
            active_family_count=len(active_families),
            abstention_rate=abstention_rate,
            family_scores=family_scores,
            extra_reasons=tuple(reason_codes),
        )
    if abs(buy_score - sell_score) <= settings.tie_tolerance:
        return _hold_result(
            "meta_strategy.aggregation.buy_sell_tie",
            total_count=total_count,
            active_count=active_count,
            active_family_count=len(active_families),
            abstention_rate=abstention_rate,
            family_scores=family_scores,
            extra_reasons=tuple(reason_codes),
        )
    if buy_score > 0.0 and sell_score > 0.0 and abs(buy_score - sell_score) < settings.minimum_conflict_edge:
        return _hold_result(
            "meta_strategy.aggregation.buy_sell_conflict",
            total_count=total_count,
            active_count=active_count,
            active_family_count=len(active_families),
            abstention_rate=abstention_rate,
            family_scores=family_scores,
            extra_reasons=tuple(reason_codes),
        )

    signal: Direction = "BUY" if buy_score > sell_score else "SELL"
    confidence = round(min(1.0, abs(buy_score - sell_score)), 6)
    reason_codes.append(f"meta_strategy.aggregation.{signal.lower()}_selected")
    return FamilyAggregationResult(
        signal=signal,
        eligible=True,
        confidence=confidence,
        buy_score=buy_score,
        sell_score=sell_score,
        hold_score=hold_score,
        active_strategy_count=active_count,
        active_family_count=len(active_families),
        abstention_rate=abstention_rate,
        family_scores=family_scores,
        reason_codes=tuple(reason_codes),
    )


def _as_contribution(
    item: SnapshotEvaluationResult | StrategyContribution,
    registry_entries: tuple[MetaStrategyRegistryEntry, ...] | list[MetaStrategyRegistryEntry],
) -> StrategyContribution:
    if isinstance(item, StrategyContribution):
        return item
    registry = {entry.strategy_id: entry for entry in registry_entries}
    entry = registry.get(item.strategy_id)
    evidence = item.evidence or {}
    family = str(item.family if item.family != "UNKNOWN" else entry.family if entry else evidence.get("family", "UNKNOWN"))
    return StrategyContribution(
        strategy_id=item.strategy_id,
        family=family,
        signal=item.signal if item.signal in {"BUY", "SELL"} else "HOLD",
        confidence=float(item.confidence),
        eligible=bool(item.eligible),
        weight=float(evidence.get("weight", 1.0)),
        canonical_influence_id=str(evidence.get("canonicalInfluenceId") or entry.canonical_influence_id if entry else item.strategy_id),
        correlation_key=str(evidence.get("correlationKey") or family),
    )


def _dedupe_aliases(contributions: tuple[StrategyContribution, ...]) -> tuple[tuple[StrategyContribution, ...], int]:
    by_id: dict[str, StrategyContribution] = {}
    deduped_count = 0
    for item in contributions:
        influence_id = item.canonical_influence_id or item.strategy_id
        existing = by_id.get(influence_id)
        if existing is None:
            by_id[influence_id] = item
            continue
        deduped_count += 1
        if _raw_contribution(item) > _raw_contribution(existing):
            by_id[influence_id] = item
    return tuple(by_id[key] for key in sorted(by_id)), deduped_count


def _family_scores(active: tuple[StrategyContribution, ...], settings: FamilyAggregationConfig) -> tuple[FamilyContribution, ...]:
    families = sorted({item.family for item in active})
    results: list[FamilyContribution] = []
    for family in families:
        family_items = tuple(item for item in active if item.family == family)
        grouped = _correlation_capped_contributions(family_items, settings)
        buy = sum(value for item, value in grouped if item.signal == "BUY")
        sell = sum(value for item, value in grouped if item.signal == "SELL")
        total = buy + sell
        capped = False
        if total > settings.family_contribution_cap:
            scale = settings.family_contribution_cap / total
            buy *= scale
            sell *= scale
            capped = True
        results.append(
            FamilyContribution(
                family=family,
                buy_score=round(buy, 6),
                sell_score=round(sell, 6),
                hold_score=0.0,
                active_strategy_count=len(family_items),
                capped=capped,
            )
        )
    return tuple(results)


def _correlation_capped_contributions(
    family_items: tuple[StrategyContribution, ...],
    settings: FamilyAggregationConfig,
) -> tuple[tuple[StrategyContribution, float], ...]:
    raw_items = tuple((item, min(settings.strategy_contribution_cap, _raw_contribution(item))) for item in family_items)
    by_group: dict[str, list[tuple[StrategyContribution, float]]] = {}
    for item, value in raw_items:
        by_group.setdefault(item.correlation_key or item.family, []).append((item, value))
    capped: list[tuple[StrategyContribution, float]] = []
    for group_items in by_group.values():
        group_total = sum(value for _, value in group_items)
        scale = settings.correlation_group_cap / group_total if group_total > settings.correlation_group_cap else 1.0
        capped.extend((item, value * scale) for item, value in group_items)
    return tuple(capped)


def _raw_contribution(item: StrategyContribution) -> float:
    return max(0.0, min(1.0, float(item.confidence))) * max(0.0, float(item.weight))


def _hold_result(
    reason_code: str,
    *,
    total_count: int = 0,
    active_count: int = 0,
    active_family_count: int = 0,
    abstention_rate: float = 1.0,
    family_scores: tuple[FamilyContribution, ...] = (),
    extra_reasons: tuple[str, ...] = (),
) -> FamilyAggregationResult:
    return FamilyAggregationResult(
        signal="HOLD",
        eligible=False,
        confidence=0.0,
        buy_score=round(sum(score.buy_score for score in family_scores), 6),
        sell_score=round(sum(score.sell_score for score in family_scores), 6),
        hold_score=round(abstention_rate if total_count else 1.0, 6),
        active_strategy_count=active_count,
        active_family_count=active_family_count,
        abstention_rate=round(abstention_rate, 6),
        family_scores=family_scores,
        reason_codes=(*extra_reasons, reason_code),
    )


__all__ = [
    "FamilyAggregationConfig",
    "FamilyAggregationResult",
    "FamilyContribution",
    "StrategyContribution",
    "aggregate_family_scores",
]
