from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.backtesting.event_replay import ReplayResult, ReplayTrade
from backend.app.domain.models import DomainModel, OperatingMode, _require_utc


VariantId = Literal["A", "B", "C", "D", "E"]


class BacktestMarketPeriod(DomainModel):
    startUtc: datetime
    endUtc: datetime
    symbols: list[str] = Field(min_length=1)

    @field_validator("startUtc", "endUtc")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def end_after_start(self) -> "BacktestMarketPeriod":
        if self.endUtc <= self.startUtc:
            raise ValueError("market period endUtc must be after startUtc")
        return self


class BacktestVariantSpec(DomainModel):
    variantId: VariantId
    name: str = Field(min_length=1)
    ensembleMode: str = Field(min_length=1)
    policyMode: str = Field(min_length=1)
    mlMode: OperatingMode
    gateMode: str = Field(min_length=1)
    referenceOnly: bool
    usesV1SignalSchema: bool
    promotionEligible: bool
    primaryPromotionBaseline: bool = False
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class DiagnosticExperimentSpec(DomainModel):
    diagnosticId: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    baselineVariantId: VariantId
    comparisonVariantIds: list[VariantId] = Field(default_factory=list)
    diagnosticsOnly: bool = False
    promotionEligible: bool = True
    explanation: str = Field(min_length=1)


class BacktestPerformanceMetrics(DomainModel):
    candidateCount: int = Field(ge=0)
    decisionCount: int = Field(ge=0)
    tradeCount: int = Field(ge=0)
    winningTrades: int = Field(ge=0)
    losingTrades: int = Field(ge=0)
    netPnl: float
    grossProfit: float = Field(ge=0)
    grossLoss: float = Field(ge=0)
    profitFactor: float | None = Field(default=None, ge=0)
    maxDrawdown: float = Field(ge=0)
    totalCosts: float = Field(ge=0)
    winRate: float = Field(ge=0, le=1)
    averagePnl: float
    returnPerUnitDrawdown: float | None = None


class BacktestFoldMetrics(DomainModel):
    foldId: str = Field(min_length=1)
    metrics: BacktestPerformanceMetrics


class BacktestMetricDelta(DomainModel):
    comparisonId: str = Field(min_length=1)
    baselineVariantId: VariantId
    comparisonVariantId: VariantId
    netPnlDelta: float
    tradeCountDelta: int
    maxDrawdownDelta: float
    profitFactorDelta: float | None
    explanation: str = Field(min_length=1)


class BacktestVariantInput(DomainModel):
    variantId: VariantId
    marketPeriod: BacktestMarketPeriod
    costAssumptions: dict[str, Any]
    foldReplayResults: dict[str, list[ReplayResult]] = Field(min_length=1)


class BacktestVariantReport(DomainModel):
    spec: BacktestVariantSpec
    marketPeriod: BacktestMarketPeriod
    costAssumptionsHash: str
    candidateUniverseHash: str
    foldMetrics: list[BacktestFoldMetrics] = Field(min_length=1)
    aggregateMetrics: BacktestPerformanceMetrics
    promotionBaselineVariantId: VariantId | None
    explanation: str = Field(min_length=1)


class ExperimentMatrixReport(DomainModel):
    experimentVersion: str
    generatedAt: datetime
    primaryPromotionBaselineVariantId: VariantId
    marketPeriod: BacktestMarketPeriod
    costAssumptionsHash: str
    candidateUniverseHash: str
    variants: list[BacktestVariantReport]
    diagnosticExperiments: list[DiagnosticExperimentSpec]
    contributionSummary: list[BacktestMetricDelta]
    reasonCodes: list[str]
    explanation: str = Field(min_length=1)

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def v1_cannot_be_promotion_baseline(self) -> "ExperimentMatrixReport":
        if self.primaryPromotionBaselineVariantId != "B":
            raise ValueError("Variant B is the only valid primary promotion baseline for V2 ML")
        v1_reports = [variant for variant in self.variants if variant.spec.variantId == "A"]
        if v1_reports and not v1_reports[0].spec.referenceOnly:
            raise ValueError("Variant A must remain reference-only")
        return self


def required_experiment_variants() -> list[BacktestVariantSpec]:
    return [
        BacktestVariantSpec(
            variantId="A",
            name="Existing V1 ensemble",
            ensembleMode="existing_v1_reference",
            policyMode="v1_reference_policy",
            mlMode=OperatingMode.OFF,
            gateMode="v1_reference_gates",
            referenceOnly=True,
            usesV1SignalSchema=True,
            promotionEligible=False,
            reasonCodes=["v1_schema_incompatible_reference_only"],
            explanation="Existing V1 ensemble is run only as a historical reference because its signal schema is incompatible with V2 training.",
        ),
        BacktestVariantSpec(
            variantId="B",
            name="Family-aware deterministic baseline",
            ensembleMode="family_aware_static_equal_weights",
            policyMode="static_baseline_policy",
            mlMode=OperatingMode.OFF,
            gateMode="global_gates_enabled",
            referenceOnly=False,
            usesV1SignalSchema=False,
            promotionEligible=True,
            primaryPromotionBaseline=True,
            reasonCodes=["primary_v2_promotion_baseline"],
            explanation="Corrected family-aware ensemble with static baseline settings; this is the primary ML promotion baseline.",
        ),
        BacktestVariantSpec(
            variantId="C",
            name="Family-aware baseline plus ML filter",
            ensembleMode="family_aware_static_equal_weights",
            policyMode="static_baseline_policy",
            mlMode=OperatingMode.FILTER,
            gateMode="global_gates_enabled",
            referenceOnly=False,
            usesV1SignalSchema=False,
            promotionEligible=True,
            explanation="Variant B with the safe ML meta-label filter allowed only to accept or reject deterministic candidates.",
        ),
        BacktestVariantSpec(
            variantId="D",
            name="Family-aware baseline plus deterministic dynamic policy",
            ensembleMode="family_aware_static_equal_weights",
            policyMode="deterministic_dynamic_trading_policy",
            mlMode=OperatingMode.OFF,
            gateMode="global_gates_enabled",
            referenceOnly=False,
            usesV1SignalSchema=False,
            promotionEligible=True,
            explanation="Corrected family-aware ensemble with deterministic dynamic trading policy and no ML influence.",
        ),
        BacktestVariantSpec(
            variantId="E",
            name="Dynamic policy plus ML filter and risk modifier",
            ensembleMode="family_aware_static_equal_weights",
            policyMode="deterministic_dynamic_trading_policy",
            mlMode=OperatingMode.ACTIVE,
            gateMode="global_gates_enabled",
            referenceOnly=False,
            usesV1SignalSchema=False,
            promotionEligible=True,
            explanation="Variant D with safe ML filtering and bounded risk modifier enabled.",
        ),
    ]


def required_diagnostic_experiments() -> list[DiagnosticExperimentSpec]:
    return [
        DiagnosticExperimentSpec(
            diagnosticId="add_one_strategy_tests",
            kind="add_one_strategy",
            baselineVariantId="B",
            explanation="Run each strategy as an add-one contribution test against the Variant B baseline universe.",
        ),
        DiagnosticExperimentSpec(
            diagnosticId="leave_one_out_strategy_tests",
            kind="leave_one_out_strategy",
            baselineVariantId="B",
            explanation="Remove each strategy from Variant B one at a time to measure incremental expectancy and drawdown effect.",
        ),
        DiagnosticExperimentSpec(
            diagnosticId="context_ablations",
            kind="context_ablation",
            baselineVariantId="B",
            explanation="Disable bounded context modules one at a time while keeping candidate timestamps, costs, and periods fixed.",
        ),
        DiagnosticExperimentSpec(
            diagnosticId="regime_filter_ablations",
            kind="regime_filter_ablation",
            baselineVariantId="B",
            explanation="Disable regime fit effects one at a time to isolate their effect on family-aware decisions.",
        ),
        DiagnosticExperimentSpec(
            diagnosticId="family_normalization_ablation",
            kind="family_normalization_ablation",
            baselineVariantId="B",
            explanation="Compare family-normalized aggregation against a non-normalized diagnostic run.",
        ),
        DiagnosticExperimentSpec(
            diagnosticId="global_gate_ablation",
            kind="global_gate_ablation",
            baselineVariantId="B",
            diagnosticsOnly=True,
            promotionEligible=False,
            explanation="Disable global gates for diagnostics only; this run is not eligible for promotion or ML baseline selection.",
        ),
        DiagnosticExperimentSpec(
            diagnosticId="static_versus_dynamic_policy_comparison",
            kind="policy_comparison",
            baselineVariantId="B",
            comparisonVariantIds=["D"],
            explanation="Compare static baseline policy against deterministic dynamic policy while holding candidates and costs fixed.",
        ),
    ]


def build_experiment_matrix_report(
    variant_inputs: list[BacktestVariantInput] | list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
    variant_specs: list[BacktestVariantSpec] | None = None,
    diagnostic_specs: list[DiagnosticExperimentSpec] | None = None,
) -> ExperimentMatrixReport:
    specs = variant_specs or required_experiment_variants()
    spec_by_id = {spec.variantId: spec for spec in specs}
    normalized_inputs = [
        item if isinstance(item, BacktestVariantInput) else BacktestVariantInput(**item)
        for item in variant_inputs
    ]
    _require_required_variants(normalized_inputs, spec_by_id)

    reports = [_variant_report(item, spec_by_id[item.variantId]) for item in normalized_inputs]
    _require_identical_period_costs_and_candidates(reports)
    reports = sorted(reports, key=lambda report: report.spec.variantId)
    by_id = {report.spec.variantId: report for report in reports}
    generated = generated_at or datetime.now(UTC)
    diagnostics = diagnostic_specs or required_diagnostic_experiments()
    first = reports[0]
    return ExperimentMatrixReport(
        experimentVersion="backtest_experiment_matrix_v1",
        generatedAt=generated,
        primaryPromotionBaselineVariantId="B",
        marketPeriod=first.marketPeriod,
        costAssumptionsHash=first.costAssumptionsHash,
        candidateUniverseHash=first.candidateUniverseHash,
        variants=reports,
        diagnosticExperiments=diagnostics,
        contributionSummary=_contribution_summary(by_id),
        reasonCodes=["v1_reference_only", "variant_b_primary_ml_promotion_baseline", "identical_period_cost_candidate_universe_required"],
        explanation=(
            "Backtest experiment matrix compares V1 reference, corrected deterministic baseline, ML filtering, "
            "deterministic dynamic policy, and bounded ML risk modification over the same market period, "
            "cost assumptions, and decision timestamp universe."
        ),
    )


def _variant_report(item: BacktestVariantInput, spec: BacktestVariantSpec) -> BacktestVariantReport:
    fold_metrics = [
        BacktestFoldMetrics(foldId=fold_id, metrics=_metrics_for_results(results))
        for fold_id, results in sorted(item.foldReplayResults.items(), key=lambda entry: entry[0])
    ]
    all_results = [result for results in item.foldReplayResults.values() for result in results]
    candidate_hash = _candidate_universe_hash(item.foldReplayResults)
    if all(metric.metrics.candidateCount == 0 for metric in fold_metrics):
        raise ValueError(f"Variant {item.variantId} has no decision candidates")
    return BacktestVariantReport(
        spec=spec,
        marketPeriod=item.marketPeriod,
        costAssumptionsHash=_stable_hash(item.costAssumptions),
        candidateUniverseHash=candidate_hash,
        foldMetrics=fold_metrics,
        aggregateMetrics=_metrics_for_results(all_results),
        promotionBaselineVariantId=None if item.variantId == "A" else "B",
        explanation=(
            f"Variant {item.variantId} report includes {len(fold_metrics)} chronological fold(s) and aggregate metrics. "
            "V1 remains reference-only." if item.variantId == "A" else
            f"Variant {item.variantId} report is compared against the Variant B V2 promotion baseline."
        ),
    )


def _metrics_for_results(results: list[ReplayResult]) -> BacktestPerformanceMetrics:
    trades = [trade for result in results for trade in result.trades]
    candidate_count = sum(len(result.snapshots) for result in results)
    decision_count = sum(result.decisionCount for result in results)
    return _metrics_for_trades(trades, candidate_count=candidate_count, decision_count=decision_count)


def _metrics_for_trades(
    trades: list[ReplayTrade],
    *,
    candidate_count: int,
    decision_count: int,
) -> BacktestPerformanceMetrics:
    pnls = [trade.pnl for trade in trades]
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    total_costs = sum(_trade_total_cost(trade) for trade in trades)
    winning = sum(1 for pnl in pnls if pnl > 0)
    losing = sum(1 for pnl in pnls if pnl < 0)
    net_pnl = sum(pnls)
    max_drawdown = _max_drawdown(pnls)
    return BacktestPerformanceMetrics(
        candidateCount=candidate_count,
        decisionCount=decision_count,
        tradeCount=len(trades),
        winningTrades=winning,
        losingTrades=losing,
        netPnl=round(net_pnl, 6),
        grossProfit=round(gross_profit, 6),
        grossLoss=round(gross_loss, 6),
        profitFactor=round(gross_profit / gross_loss, 6) if gross_loss else None,
        maxDrawdown=round(max_drawdown, 6),
        totalCosts=round(total_costs, 6),
        winRate=round(winning / len(trades), 6) if trades else 0.0,
        averagePnl=round(net_pnl / len(trades), 6) if trades else 0.0,
        returnPerUnitDrawdown=round(net_pnl / max_drawdown, 6) if max_drawdown else None,
    )


def _contribution_summary(by_id: dict[str, BacktestVariantReport]) -> list[BacktestMetricDelta]:
    return [
        _delta(
            comparison_id="strategy_repair_reference_delta",
            baseline=by_id["A"],
            comparison=by_id["B"],
            explanation="Variant B minus Variant A isolates the strategy-repair reference delta; A is not a promotion baseline.",
        ),
        _delta(
            comparison_id="ml_filter_delta",
            baseline=by_id["B"],
            comparison=by_id["C"],
            explanation="Variant C minus Variant B isolates the contribution of the ML trade filter.",
        ),
        _delta(
            comparison_id="dynamic_policy_delta",
            baseline=by_id["B"],
            comparison=by_id["D"],
            explanation="Variant D minus Variant B isolates deterministic dynamic trading policy contribution.",
        ),
        _delta(
            comparison_id="ml_filter_risk_modifier_delta",
            baseline=by_id["D"],
            comparison=by_id["E"],
            explanation="Variant E minus Variant D isolates bounded ML filter and risk-modifier contribution.",
        ),
    ]


def _delta(
    *,
    comparison_id: str,
    baseline: BacktestVariantReport,
    comparison: BacktestVariantReport,
    explanation: str,
) -> BacktestMetricDelta:
    baseline_pf = baseline.aggregateMetrics.profitFactor
    comparison_pf = comparison.aggregateMetrics.profitFactor
    return BacktestMetricDelta(
        comparisonId=comparison_id,
        baselineVariantId=baseline.spec.variantId,
        comparisonVariantId=comparison.spec.variantId,
        netPnlDelta=round(comparison.aggregateMetrics.netPnl - baseline.aggregateMetrics.netPnl, 6),
        tradeCountDelta=comparison.aggregateMetrics.tradeCount - baseline.aggregateMetrics.tradeCount,
        maxDrawdownDelta=round(comparison.aggregateMetrics.maxDrawdown - baseline.aggregateMetrics.maxDrawdown, 6),
        profitFactorDelta=round(comparison_pf - baseline_pf, 6) if comparison_pf is not None and baseline_pf is not None else None,
        explanation=explanation,
    )


def _require_required_variants(inputs: list[BacktestVariantInput], spec_by_id: dict[str, BacktestVariantSpec]) -> None:
    expected = {"A", "B", "C", "D", "E"}
    actual = {item.variantId for item in inputs}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise ValueError(f"experiment matrix is missing required variants: {', '.join(missing)}")
    if extra:
        raise ValueError(f"experiment matrix contains unknown variants: {', '.join(extra)}")
    missing_specs = sorted(actual - set(spec_by_id))
    if missing_specs:
        raise ValueError(f"experiment matrix has no variant spec for: {', '.join(missing_specs)}")


def _require_identical_period_costs_and_candidates(reports: list[BacktestVariantReport]) -> None:
    period_hashes = {_stable_hash(report.marketPeriod) for report in reports}
    cost_hashes = {report.costAssumptionsHash for report in reports}
    candidate_hashes = {report.candidateUniverseHash for report in reports}
    if len(period_hashes) != 1:
        raise ValueError("all experiment variants must use identical market periods")
    if len(cost_hashes) != 1:
        raise ValueError("all experiment variants must use identical cost assumptions")
    if len(candidate_hashes) != 1:
        raise ValueError("all experiment variants must use an identical decision timestamp universe")


def _candidate_universe_hash(fold_replay_results: dict[str, list[ReplayResult]]) -> str:
    candidates: list[dict[str, str]] = []
    for fold_id, results in sorted(fold_replay_results.items(), key=lambda entry: entry[0]):
        for result in sorted(results, key=lambda item: (item.symbol, item.sessionDate.isoformat())):
            for snapshot in result.snapshots:
                candidates.append(
                    {
                        "foldId": fold_id,
                        "symbol": snapshot.symbol,
                        "decisionTimestampUtc": snapshot.decisionTimestampUtc.isoformat(),
                    }
                )
    return _stable_hash(candidates)


def _trade_total_cost(trade: ReplayTrade) -> float:
    if "total" in trade.costs:
        return float(trade.costs["total"])
    return float(sum(trade.costs.values()))


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _stable_hash(value: Any) -> str:
    payload = json.dumps(_to_jsonable(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value
