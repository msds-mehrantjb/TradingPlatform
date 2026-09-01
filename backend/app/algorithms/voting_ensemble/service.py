"""Isolated Voting Ensemble orchestration layer."""

from __future__ import annotations

import inspect
from time import perf_counter
from datetime import date, datetime, timezone
from typing import Any, Callable, Protocol

from backend.app.algorithms.voting_ensemble.ensemble.family_aware import FAMILY_ORDER, FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.algorithms.voting_ensemble.reliability.estimator import VotingEnsembleReliabilityEstimator
from backend.app.algorithms.voting_ensemble.reliability.models import (
    StrategyReliabilityEstimate,
    VotingEnsembleReliabilityConfig,
    VotingEnsembleReliabilityObservation,
)
from backend.app.algorithms.voting_ensemble.execution_adapter import VotingEnsembleExecutionAdapter
from backend.app.algorithms.voting_ensemble.session_policy import (
    apply_session_policy,
    session_policy_from_payload,
)
from backend.app.algorithms.voting_ensemble.execution_economics import build_execution_economics
from backend.app.algorithms.voting_ensemble.gates import VotingEnsembleLocalGateEngine
from backend.app.algorithms.voting_ensemble.intelligence_capture import VotingEnsembleCaptureWriter, capture_operational_event, capture_voting_ensemble_evaluation
from backend.app.algorithms.voting_ensemble.models import (
    AlgoSignal,
    FeatureValue,
    VotingContextConfirmation,
    VotingEnsembleEvaluateRequest,
    VotingEnsembleEvaluateResponse,
    VotingStrategyVote,
)
from backend.app.algorithms.voting_ensemble.snapshot.builder import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.atr_overextension_reversion import AtrOverextensionReversionStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.bollinger_band_reversion import BollingerBandReversionStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.failed_breakout_reversal import SnapshotFailedBreakoutReversalStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.first_pullback_after_open import SnapshotFirstPullbackAfterOpenStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.gap_continuation_fade import GapContinuationFadeStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.liquidity_sweep_reversal import SnapshotLiquiditySweepReversalStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.multi_timeframe_trend_alignment import SnapshotMultiTimeframeTrendAlignmentStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import DirectionalStrategySignal
from backend.app.algorithms.voting_ensemble.strategies.directional.vwap_trend_continuation import VwapTrendContinuationStrategy
from backend.app.algorithms.voting_ensemble.strategies.context.pipeline import VotingEnsembleContextPipeline
from backend.app.algorithms.voting_ensemble.strategies.regime.adx_atr_regime_classifier import AdxAtrRegimeClassifier
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    VOTING_ENSEMBLE_MODULE_INVENTORY,
    StrategyCollection,
    active_module_ids,
    canonical_strategy_id,
    shadow_module_ids,
    validate_voting_ensemble_inventory_startup,
    voting_ensemble_inventory_status,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.app.algorithms.voting_ensemble.risk_budget import resolve_voting_ensemble_risk_budget
from backend.app.domain.models import AccountRiskState, BaselineTradingSettings, ContextSignal, Direction, DynamicPolicyBounds, EffectiveTradePolicy, EnsembleDecision, GateStatus, GlobalGateDecision, HardRiskLimits, OperatingMode, OrderPlan, Signal, StrategyFamily, StrategyRole, StrategySignal, TradeCandidate
from backend.app.gates import GlobalGateInput


VOTING_ENSEMBLE_SERVICE_VERSION = "voting_ensemble_backend_v2"
StrategyEvaluator = Callable[[VotingEnsembleEvaluateRequest], VotingStrategyVote]
FAMILY_AWARE_ENGINE = FamilyAwareDeterministicEnsemble()
RELIABILITY_ESTIMATOR = VotingEnsembleReliabilityEstimator()
REGIME_CLASSIFIER = AdxAtrRegimeClassifier()
LOCAL_GATE_ENGINE = VotingEnsembleLocalGateEngine()
LOCAL_SAFETY_MODULES: tuple[VotingEnsembleLocalGateEngine, ...] = (LOCAL_GATE_ENGINE,)
CONTEXT_PIPELINE = VotingEnsembleContextPipeline()
CAPTURE_WRITER = VotingEnsembleCaptureWriter(auto_start=True)
EXECUTION_ADAPTER = VotingEnsembleExecutionAdapter()


class SnapshotDirectionalStrategy(Protocol):
    strategyId: str

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, correlation_id: str) -> DirectionalStrategySignal:
        ...


def _fail_closed_response(snapshot: VotingEnsembleEvaluationSnapshot, service_version: str) -> dict[str, Any]:
    reason_codes = (
        "voting_ensemble.evaluate.fail_closed_data_readiness",
        snapshot.snapshotHash,
        *snapshot.dataReadiness.mandatoryFailures,
        *snapshot.dataReadiness.staleInputs,
        *snapshot.dataReadiness.malformedInputs,
    )
    votes = _fail_closed_votes(StrategyCollection.DIRECTIONAL, reason_codes)
    response = VotingEnsembleEvaluateResponse(
        service_version=service_version,
        symbol=snapshot.symbol,
        evaluated_at=snapshot.evaluationTimestamp,
        data_timestamp=snapshot.evaluationTimestamp,
        final_signal="Hold",
        votes=votes,
        context_signals=(),
        context_confirmation=VotingContextConfirmation(
            outcome="not_applicable",
            detail="Voting Ensemble failed closed because mandatory point-in-time market data was missing, stale, malformed, or future-dated.",
            evidence=reason_codes,
            confirmations=0,
            conflicts=0,
        ),
        counts=_counts(votes),
        eligible_counts={"Buy": 0, "Sell": 0, "Hold": 0},
        family_scores={},
        base_score=0.0,
        context_adjusted_score=0.0,
        context_agreements=0,
        context_conflicts=0,
        context_adjustment_reason="fail_closed_data_readiness",
        family_support={},
        family_opposition={},
        eligible_strategy_count=0,
        safety_gate_failed=True,
        blocked_gate_ids=("snapshot.data_readiness",),
        decision_trace=(
            _trace_step("snapshot_data_health", "blocked", reason_codes),
        ),
        local_gate_decision=None,
        upstream_global_gate_decision=None,
        reason_codes=reason_codes,
    )
    return response.model_dump(mode="json")


def _fail_closed_votes(collection: StrategyCollection, reason_codes: tuple[str, ...]) -> tuple[VotingStrategyVote, ...]:
    blockers = ", ".join(_readiness_blocker_codes(reason_codes))
    return _inventory_blocker_votes(
        collection,
        reason=f"Snapshot data-readiness failed closed before strategy evaluation: {blockers}.",
        reason_code="voting_ensemble.strategy.fail_closed_data_readiness",
        reason_codes=reason_codes,
    )


def _inventory_blocker_votes(
    collection: StrategyCollection,
    *,
    reason: str,
    reason_code: str,
    reason_codes: tuple[str, ...],
) -> tuple[VotingStrategyVote, ...]:
    modules_by_id = {module.strategyId: module for module in VOTING_ENSEMBLE_MODULE_INVENTORY.modules}
    votes: list[VotingStrategyVote] = []
    for module_id in active_module_ids(collection):
        module = modules_by_id[module_id]
        role = "context" if collection == StrategyCollection.CONTEXT else "directional"
        votes.append(
            VotingStrategyVote(
                strategy=module.strategyName,
                family=_vote_family_from_inventory(module.family),
                role=role,
                signal="Hold",
                direction=0,
                confidence=0.0,
                active=True,
                eligible=False,
                dataReady=False,
                regimeFit=0.0,
                reliability=0.0,
                reason=reason,
                features={
                    "strategyId": module.strategyId,
                    "strategyVersion": module.strategyVersion,
                    "reasonCode": reason_code,
                    "reasonCodes": ",".join(reason_codes),
                    "inventoryCollection": collection.value,
                    "lifecycleStatus": module.lifecycleStatus,
                },
            )
        )
    return tuple(votes)


def _vote_family_from_inventory(family: str) -> str:
    normalized = str(family).lower()
    if normalized == "market_context":
        return "event"
    return normalized


def _readiness_blocker_codes(reason_codes: tuple[str, ...]) -> tuple[str, ...]:
    blockers = [
        code
        for code in reason_codes
        if code != "voting_ensemble.evaluate.fail_closed_data_readiness" and not _looks_like_snapshot_hash(code)
    ]
    return tuple(blockers or ("unknown_data_readiness_blocker",))


def _looks_like_snapshot_hash(value: str) -> bool:
    return len(value) >= 8 and all(character in "0123456789abcdef" for character in value.lower())


class VotingEnsembleService:
    version = VOTING_ENSEMBLE_SERVICE_VERSION

    def evaluate(self, payload: dict) -> dict:
        snapshot_started = perf_counter()
        snapshot = build_live_paper_snapshot(payload)
        snapshot_duration_ms = _elapsed_ms(snapshot_started)
        if not snapshot.dataReadiness.ready:
            response_payload = _fail_closed_response(snapshot, self.version)
            capture_operational_event(
                writer=CAPTURE_WRITER,
                event_type="error_recovery_event",
                payload={"response": response_payload, "dataReadiness": snapshot.dataReadiness.model_dump(mode="json")},
                correlation_id=_payload_identifier(payload, "correlationId", "correlation_id") or snapshot.snapshotHash,
                job_id=_payload_identifier(payload, "jobId", "job_id"),
                settings_hash=snapshot.settingsHash,
                snapshot_timestamp=snapshot.evaluationTimestamp,
            )
            return response_payload

        settings = resolve_one_minute_trading_settings(_settings_payload(payload))
        request = VotingEnsembleEvaluateRequest.model_validate(snapshot.to_evaluate_payload())
        regime_state = REGIME_CLASSIFIER.evaluate_snapshot(snapshot)
        upstream_global_gate = _upstream_global_gate_decision(snapshot)
        pre_gate_started = perf_counter()
        pre_gate_engine_decision = LOCAL_GATE_ENGINE.evaluate(
            _local_gate_input(
                snapshot=snapshot,
                settings_hash=settings.configurationHash,
                settings=settings,
                order_intent="strategy_evaluation",
                upstream_global_gate=upstream_global_gate,
                regime_state=regime_state,
                ensemble_decision=None,
                candidate=None,
                context_signals=(),
                execution_economics=None,
            )
        )
        gate_duration_ms = _elapsed_ms(pre_gate_started)
        pre_gate = pre_gate_engine_decision.to_global_gate_decision()
        if not pre_gate.eligible:
            response_payload = _safety_blocked_response(
                snapshot=snapshot,
                service_version=self.version,
                regime_state=regime_state,
                local_gate=pre_gate,
                upstream_global_gate=upstream_global_gate,
                trace=(
                    _trace_step("snapshot_data_health", "passed", snapshot.dataReadiness.reasonCodes),
                    _trace_step("global_hard_gates", "blocked" if _global_gate_blocks(upstream_global_gate) else "passed", _gate_reason_codes(upstream_global_gate)),
                    _trace_step("voting_ensemble_operational_and_regime_safety", "blocked", pre_gate.reasonCodes),
                ),
            )
            _capture_blocked_gates(payload, snapshot, settings.configurationHash, upstream_global_gate, pre_gate)
            return response_payload
        strategy_started = perf_counter()
        directional_votes = tuple(_evaluate_directional(module, snapshot, request, regime_state) for module in DIRECTIONAL_STRATEGIES)
        shadow_directional_votes = tuple(_evaluate_directional(module, snapshot, request, regime_state, active=False) for module in SHADOW_DIRECTIONAL_STRATEGIES)
        context_result = _evaluate_context_pipeline(snapshot)
        context_signals = context_result.active
        shadow_context_signals = context_result.shadow
        strategy_duration_ms = _elapsed_ms(strategy_started)
        # Session policy sits above the voters: every strategy has already evaluated and
        # kept its reasoning, and this only decides whose vote counts in this segment.
        # Blocked votes are marked ineligible rather than dropped, so the decision record
        # still shows what they said and why they did not count.
        directional_votes, session_policy_decision = apply_session_policy(
            directional_votes,
            session_segment=_session_segment(snapshot),
            settings=session_policy_from_payload(_session_policy_payload(payload, settings)),
        )
        eligible_votes = tuple(vote for vote in directional_votes if vote.eligible and vote.dataReady)
        counts = _counts(directional_votes)
        eligible_counts = _counts(eligible_votes)
        aggregation_started = perf_counter()
        decision = _aggregate_with_family_engine(directional_votes, context_signals, snapshot, regime_state, pre_gate, settings=settings, payload=payload)
        aggregation_duration_ms = _elapsed_ms(aggregation_started)
        candidate = _candidate_from_decision(snapshot, decision, settings)
        latency_measurements = {
            "snapshotBuildDurationMs": snapshot_duration_ms,
            "strategyEvaluationDurationMs": strategy_duration_ms,
            "aggregationDurationMs": aggregation_duration_ms,
            "gateDurationMs": gate_duration_ms,
            "decisionDeadlineExpired": _decision_deadline_expired(snapshot, settings),
        }
        execution_economics = build_execution_economics(
            snapshot=snapshot,
            decision=decision,
            candidate=candidate,
            settings=settings,
            latency_measurements=latency_measurements,
        )
        post_gate_started = perf_counter()
        post_gate_engine_decision = LOCAL_GATE_ENGINE.evaluate(
            _local_gate_input(
                snapshot=snapshot,
                settings_hash=settings.configurationHash,
                settings=settings,
                upstream_global_gate=upstream_global_gate,
                regime_state=regime_state,
                ensemble_decision=decision,
                candidate=candidate,
                context_signals=tuple(_context_signal_from_vote(vote, _utc_timestamp(snapshot.evaluationTimestamp), _utc_timestamp(snapshot.evaluationTimestamp).date(), snapshot.settingsHash) for vote in context_signals),
                execution_economics=execution_economics.model_dump(mode="json") if execution_economics else None,
            )
        )
        gate_duration_ms = round(gate_duration_ms + _elapsed_ms(post_gate_started), 4)
        post_gate = post_gate_engine_decision.to_global_gate_decision()
        risk_budget = _risk_budget_for_candidate(
            snapshot=snapshot,
            settings=settings,
            decision=decision,
            candidate=candidate,
            local_gate=post_gate,
            execution_economics=execution_economics.model_dump(mode="json") if execution_economics else None,
        )
        if candidate is not None and risk_budget is not None:
            candidate = candidate.model_copy(
                update={
                    "quantity": risk_budget.quantity,
                    "features": {**candidate.features, "riskBudget": risk_budget.to_payload()},
                    "reasonCodes": [*candidate.reasonCodes, *risk_budget.reason_codes],
                }
            )
        risk_budget_blocked_gate_ids = ("risk_budget.quantity",) if candidate is not None and risk_budget is not None and risk_budget.quantity <= 0 else ()
        final_signal = _algo_signal_from_domain(decision.signal) if post_gate.eligible and not risk_budget_blocked_gate_ids else "Hold"
        account_state = _account_risk_state(snapshot)
        order_plan = _order_plan_for_candidate(
            candidate=candidate if final_signal != "Hold" else None,
            settings=settings,
            gate_decision=post_gate,
            account=account_state,
            risk_budget=risk_budget.to_payload() if risk_budget else None,
            evaluated_at=_utc_timestamp(snapshot.evaluationTimestamp),
        )
        context_confirmation = _context_confirmation(final_signal, context_signals)
        context_agreements = sum(1 for row in decision.contextAdjustments if float(row.get("adjustment") or 0.0) > 0)
        context_conflicts = sum(1 for row in decision.contextAdjustments if float(row.get("adjustment") or 0.0) < 0)
        blocked_gate_ids = (*_blocked_gate_ids(post_gate), *risk_budget_blocked_gate_ids)
        decision_trace = (
            _trace_step("snapshot_data_health", "passed", snapshot.dataReadiness.reasonCodes),
            _trace_step("global_hard_gates", "blocked" if _global_gate_blocks(upstream_global_gate) else "passed", _gate_reason_codes(upstream_global_gate)),
            _trace_step("voting_ensemble_operational_safety", "passed", pre_gate.reasonCodes),
            _trace_step("voting_ensemble_regime_event_permission", "passed", tuple(regime_state.features.get("reasonCodes") or ())),
            _trace_step("directional_evaluation", "passed", tuple(vote.features.get("reasonCode", "") for vote in directional_votes)),
            _trace_step("family_aggregation", "passed" if decision.signal != Signal.HOLD.value else "held", tuple(decision.reasonCodes)),
            _trace_step("cost_tradability_and_local_risk", "blocked" if blocked_gate_ids else "passed", post_gate.reasonCodes, blocked_gate_ids=blocked_gate_ids),
            _trace_step("dynamic_profile", "passed" if settings.resolvedTradingProfile.entryPermission == "allow_new_entries" else "blocked", settings.reasonCodes),
            _trace_step("risk_sizing", "blocked" if risk_budget_blocked_gate_ids else "passed", risk_budget.reason_codes if risk_budget else ("voting_ensemble.risk_budget.no_candidate",), blocked_gate_ids=risk_budget_blocked_gate_ids),
            _trace_step("order_planning_execution", "pending", ("voting_ensemble.service.evaluate_does_not_submit_orders",)),
        )
        response = VotingEnsembleEvaluateResponse(
            service_version=self.version,
            symbol=snapshot.symbol,
            evaluated_at=datetime.now(timezone.utc),
            data_timestamp=snapshot.evaluationTimestamp,
            final_signal=final_signal,
            votes=directional_votes,
            shadow_directional_votes=shadow_directional_votes,
            context_signals=context_signals,
            shadow_context_signals=shadow_context_signals,
            reliability_scope=reliability_scope(snapshot, regime_state),
            context_confirmation=context_confirmation,
            counts=counts,
            eligible_counts=eligible_counts,
            family_scores=_family_scores_from_decision(decision),
            base_score=decision.rawScore,
            context_adjusted_score=decision.finalScore,
            context_agreements=context_agreements,
            context_conflicts=context_conflicts,
            context_adjustment_reason="family_aware_engine",
            family_support={_family_key(family): 1 for family in decision.supportingFamilies},
            family_opposition={_family_key(family): 1 for family in decision.opposingFamilies},
            eligible_strategy_count=decision.eligibleStrategyCount,
            safety_gate_failed=bool(blocked_gate_ids),
            blocked_gate_ids=blocked_gate_ids,
            decision_trace=decision_trace,
            local_gate_decision=post_gate.model_dump(mode="json"),
            upstream_global_gate_decision=upstream_global_gate.model_dump(mode="json") if upstream_global_gate else None,
            resolved_trading_profile=settings.resolvedTradingProfile.model_dump(mode="json"),
            execution_economics=_execution_economics_with_gate_duration(execution_economics, gate_duration_ms),
            risk_budget=risk_budget.to_payload() if risk_budget else None,
            candidate=candidate.model_dump(mode="json") if candidate else None,
            order_plan=order_plan.model_dump(mode="json") if order_plan else None,
            reason_codes=(
                "voting_ensemble.evaluate.completed",
                *tuple(decision.reasonCodes),
                *tuple(regime_state.features.get("reasonCodes") or ()),
                *tuple(post_gate.reasonCodes),
                *tuple(risk_budget.reason_codes if risk_budget else ()),
                snapshot.snapshotHash,
            ),
        )
        response_payload = response.model_dump(mode="json")
        capture_voting_ensemble_evaluation(
            writer=CAPTURE_WRITER,
            snapshot=snapshot,
            settings=settings,
            regime_state=regime_state,
            directional_votes=directional_votes,
            shadow_directional_votes=shadow_directional_votes,
            context_signals=context_signals,
            shadow_context_signals=shadow_context_signals,
            decision=decision,
            upstream_global_gate=upstream_global_gate,
            local_gate=post_gate,
            execution_economics=response_payload.get("execution_economics"),
            risk_budget=response_payload.get("risk_budget"),
            response=response_payload,
            job_id=_payload_identifier(payload, "jobId", "job_id"),
            correlation_id=_payload_identifier(payload, "correlationId", "correlation_id") or snapshot.snapshotHash,
        )
        return response_payload

    def status(self) -> dict:
        runtime = voting_ensemble_service_runtime_bindings()
        return {
            "algorithmId": "voting_ensemble",
            "serviceVersion": self.version,
            "status": "ready" if runtime["validation"]["valid"] else "fail_closed",
            "isolated": True,
            "moduleInventory": VOTING_ENSEMBLE_MODULE_INVENTORY.model_dump(mode="json"),
            "inventoryStatus": runtime["inventoryStatus"],
            "runtimeBindings": runtime["actualRuntimeBindings"],
            "directionalStrategies": list(runtime["actualRuntimeBindings"][StrategyCollection.DIRECTIONAL.value]),
            "shadowDirectionalStrategies": list(shadow_module_ids(StrategyCollection.DIRECTIONAL)),
            "dynamicRoleStrategies": [],
            "contextSignals": list(runtime["actualRuntimeBindings"][StrategyCollection.CONTEXT.value]),
            "shadowContextSignals": list(shadow_module_ids(StrategyCollection.CONTEXT)),
            "removedVoters": ["Ensemble Strategy Voting"],
            "reasonCodes": ["voting_ensemble.api.ready"],
        }


def _settings_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("tradingSettings", "settings"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return None


def _safety_blocked_response(
    *,
    snapshot: VotingEnsembleEvaluationSnapshot,
    service_version: str,
    regime_state: RegimeState,
    local_gate: GlobalGateDecision,
    upstream_global_gate: GlobalGateDecision | None,
    trace: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    blocked_gate_ids = _blocked_gate_ids(local_gate)
    votes = _safety_blocked_votes(StrategyCollection.DIRECTIONAL, tuple(local_gate.reasonCodes))
    context_signals = _safety_blocked_votes(StrategyCollection.CONTEXT, tuple(local_gate.reasonCodes))
    response = VotingEnsembleEvaluateResponse(
        service_version=service_version,
        symbol=snapshot.symbol,
        evaluated_at=datetime.now(timezone.utc),
        data_timestamp=snapshot.evaluationTimestamp,
        final_signal="Hold",
        votes=votes,
        context_signals=context_signals,
        context_confirmation=VotingContextConfirmation(
            outcome="not_applicable",
            detail="Voting Ensemble failed closed before directional evaluation because mandatory local safety gates blocked automatic entries.",
            evidence=tuple(local_gate.reasonCodes),
            confirmations=0,
            conflicts=0,
        ),
        counts=_counts(votes),
        eligible_counts={"Buy": 0, "Sell": 0, "Hold": 0},
        family_scores={},
        base_score=0.0,
        context_adjusted_score=0.0,
        context_agreements=0,
        context_conflicts=0,
        context_adjustment_reason="voting_ensemble_local_gates",
        family_support={},
        family_opposition={},
        eligible_strategy_count=0,
        safety_gate_failed=True,
        blocked_gate_ids=blocked_gate_ids,
        decision_trace=trace,
        local_gate_decision=local_gate.model_dump(mode="json"),
        upstream_global_gate_decision=upstream_global_gate.model_dump(mode="json") if upstream_global_gate else None,
        reason_codes=(
            "voting_ensemble.evaluate.blocked_by_local_safety",
            *tuple(local_gate.reasonCodes),
            *tuple(regime_state.features.get("reasonCodes") or ()),
            snapshot.snapshotHash,
        ),
    )
    return response.model_dump(mode="json")


def _safety_blocked_votes(collection: StrategyCollection, reason_codes: tuple[str, ...]) -> tuple[VotingStrategyVote, ...]:
    blockers = ", ".join(reason_codes or ("local_safety_gate_blocked",))
    return _inventory_blocker_votes(
        collection,
        reason="Voting Ensemble local safety gates blocked automatic entry before strategy evaluation: " + blockers + ".",
        reason_code="voting_ensemble.strategy.blocked_by_local_safety",
        reason_codes=reason_codes,
    )


def evaluate_multi_timeframe_trend(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return _evaluate_request_with_strategy(request, SnapshotMultiTimeframeTrendAlignmentStrategy())


def evaluate_first_pullback_after_open(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return _evaluate_request_with_strategy(request, SnapshotFirstPullbackAfterOpenStrategy())


def evaluate_failed_breakout_strategy(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return _evaluate_request_with_strategy(request, SnapshotFailedBreakoutReversalStrategy())


def evaluate_liquidity_sweep_reversal(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return _evaluate_request_with_strategy(request, SnapshotLiquiditySweepReversalStrategy())


def evaluate_bollinger_band_reversion(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return _evaluate_request_with_strategy(request, BollingerBandReversionStrategy())


def evaluate_atr_overextension_reversion(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return _evaluate_request_with_strategy(request, AtrOverextensionReversionStrategy())


def evaluate_economic_event_reaction(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    context = request.market_context or {}
    event = context.get("event") if isinstance(context.get("event"), dict) else {}
    if event and event.get("name") and str(event.get("name")).lower() not in {"none", "no_event"}:
        return _vote("Economic Event Context", "event", "Hold", 15, "Economic event state is present and remains context-only.", "voting_ensemble.event.context_only", role="context")
    return _vote("Economic Event Context", "event", "Hold", 10, "No actionable economic-event context.", "voting_ensemble.event.no_context", role="context")


def evaluate_relative_strength(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    if len(request.candles) < 2 or len(request.qqq_candles) < 2 or len(request.iwm_candles) < 2:
        return _vote("Relative Strength vs QQQ/IWM", "event", "Hold", 10, "QQQ/IWM point-in-time data is unavailable.", "voting_ensemble.relative_strength.unavailable", role="context", data_ready=False)
    spy = _simple_return(request.candles)
    qqq = _simple_return(request.qqq_candles)
    iwm = _simple_return(request.iwm_candles)
    spread = spy - ((qqq + iwm) / 2)
    if spread > 0.001:
        signal: AlgoSignal = "Buy"
        reason = "SPY is stronger than QQQ/IWM over the available point-in-time window."
        code = "voting_ensemble.relative_strength.supports_buy"
    elif spread < -0.001:
        signal = "Sell"
        reason = "SPY is weaker than QQQ/IWM over the available point-in-time window."
        code = "voting_ensemble.relative_strength.supports_sell"
    else:
        signal = "Hold"
        reason = "SPY relative strength is neutral."
        code = "voting_ensemble.relative_strength.neutral"
    return _vote("Relative Strength vs QQQ/IWM", "event", signal, 45, reason, code, role="context", features={"relativeStrengthSpread": round(spread, 6)})


def evaluate_market_breadth(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    feed = request.external_breadth_feed or {}
    advancing = feed.get("percentageAdvancing")
    if not isinstance(advancing, int | float):
        return _vote("Market Breadth Momentum", "event", "Hold", 10, "External breadth feed is unavailable.", "voting_ensemble.market_breadth.unavailable", role="context", data_ready=False)
    if advancing >= 0.58:
        return _vote("Market Breadth Momentum", "event", "Buy", 45, "Breadth supports long-side decisions.", "voting_ensemble.market_breadth.supports_buy", role="context", features={"percentageAdvancing": round(float(advancing), 4)})
    if advancing <= 0.42:
        return _vote("Market Breadth Momentum", "event", "Sell", 45, "Breadth supports short-side decisions.", "voting_ensemble.market_breadth.supports_sell", role="context", features={"percentageAdvancing": round(float(advancing), 4)})
    return _vote("Market Breadth Momentum", "event", "Hold", 35, "Breadth is neutral.", "voting_ensemble.market_breadth.neutral", role="context", features={"percentageAdvancing": round(float(advancing), 4)})


STRATEGY_EVALUATORS_BY_ID: dict[str, StrategyEvaluator] = {
    "multi_timeframe_trend_alignment": evaluate_multi_timeframe_trend,
    "first_pullback_after_open": evaluate_first_pullback_after_open,
    "failed_breakout_reversal": evaluate_failed_breakout_strategy,
    "liquidity_sweep_reversal": evaluate_liquidity_sweep_reversal,
    "bollinger_band_reversion": evaluate_bollinger_band_reversion,
    "atr_overextension_reversion": evaluate_atr_overextension_reversion,
    "economic_event_context": evaluate_economic_event_reaction,
    "relative_strength_qqq_iwm": evaluate_relative_strength,
    "market_breadth_momentum": evaluate_market_breadth,
}

SNAPSHOT_STRATEGY_INSTANCES: dict[str, SnapshotDirectionalStrategy] = {
    "multi_timeframe_trend_alignment": SnapshotMultiTimeframeTrendAlignmentStrategy(),
    "first_pullback_after_open": SnapshotFirstPullbackAfterOpenStrategy(),
    "failed_breakout_reversal": SnapshotFailedBreakoutReversalStrategy(),
    "liquidity_sweep_reversal": SnapshotLiquiditySweepReversalStrategy(),
    "bollinger_band_reversion": BollingerBandReversionStrategy(),
    "atr_overextension_reversion": AtrOverextensionReversionStrategy(),
    "opening_range_breakout": OpeningRangeBreakoutStrategy(),
    "vwap_trend_continuation": VwapTrendContinuationStrategy(),
    "gap_continuation_fade": GapContinuationFadeStrategy(),
}

# The registry is the single source of truth for which directional modules are live;
# these views follow it so a lifecycle promotion needs no matching edit here.
SNAPSHOT_STRATEGIES_BY_ID: dict[str, SnapshotDirectionalStrategy] = {
    module_id: SNAPSHOT_STRATEGY_INSTANCES[module_id]
    for module_id in active_module_ids(StrategyCollection.DIRECTIONAL)
}

SHADOW_SNAPSHOT_STRATEGIES_BY_ID: dict[str, SnapshotDirectionalStrategy] = {
    module_id: SNAPSHOT_STRATEGY_INSTANCES[module_id]
    for module_id in shadow_module_ids(StrategyCollection.DIRECTIONAL)
}

DIRECTIONAL_STRATEGIES: tuple[SnapshotDirectionalStrategy | StrategyEvaluator, ...] = tuple(
    SNAPSHOT_STRATEGIES_BY_ID[module_id] for module_id in active_module_ids(StrategyCollection.DIRECTIONAL)
)
SHADOW_DIRECTIONAL_STRATEGIES: tuple[SnapshotDirectionalStrategy | StrategyEvaluator, ...] = tuple(
    SHADOW_SNAPSHOT_STRATEGIES_BY_ID[module_id] for module_id in shadow_module_ids(StrategyCollection.DIRECTIONAL)
)
DYNAMIC_ROLE_STRATEGIES: tuple[StrategyEvaluator, ...] = ()
CONTEXT_STRATEGIES: tuple[StrategyEvaluator, ...] = tuple(
    STRATEGY_EVALUATORS_BY_ID[module_id] for module_id in active_module_ids(StrategyCollection.CONTEXT)
)


def _evaluate_context_pipeline(snapshot: VotingEnsembleEvaluationSnapshot):
    if not CONTEXT_STRATEGIES:
        return CONTEXT_PIPELINE.evaluate(snapshot, active_module_ids=(), shadow_module_ids=())
    return CONTEXT_PIPELINE.evaluate(
        snapshot,
        active_module_ids=active_module_ids(StrategyCollection.CONTEXT),
        shadow_module_ids=shadow_module_ids(StrategyCollection.CONTEXT),
    )


def voting_ensemble_service_runtime_bindings() -> dict[str, Any]:
    actual_runtime_bindings = {
        StrategyCollection.DIRECTIONAL.value: tuple(_runtime_strategy_id(module) for module in DIRECTIONAL_STRATEGIES),
        StrategyCollection.CONTEXT.value: active_module_ids(StrategyCollection.CONTEXT) if CONTEXT_STRATEGIES else (),
        StrategyCollection.REGIME.value: tuple(active_module_ids(StrategyCollection.REGIME)),
        StrategyCollection.SAFETY.value: tuple(_runtime_strategy_id(module) for module in LOCAL_SAFETY_MODULES),
        StrategyCollection.AGGREGATOR.value: tuple(active_module_ids(StrategyCollection.AGGREGATOR)),
        StrategyCollection.TRADING_SETTINGS.value: tuple(active_module_ids(StrategyCollection.TRADING_SETTINGS)),
        StrategyCollection.RISK_BUDGET.value: tuple(active_module_ids(StrategyCollection.RISK_BUDGET)),
        StrategyCollection.ORDER_PLANNER.value: tuple(active_module_ids(StrategyCollection.ORDER_PLANNER)),
        StrategyCollection.EXECUTION_ADAPTER.value: tuple(active_module_ids(StrategyCollection.EXECUTION_ADAPTER)),
        StrategyCollection.BACKTEST_REPLAY_ADAPTER.value: tuple(active_module_ids(StrategyCollection.BACKTEST_REPLAY_ADAPTER)),
        StrategyCollection.BACKGROUND_WORKER.value: tuple(active_module_ids(StrategyCollection.BACKGROUND_WORKER)),
    }
    validation = validate_voting_ensemble_inventory_startup(actual_runtime_bindings)
    return {
        "actualRuntimeBindings": actual_runtime_bindings,
        "validation": validation,
        "inventoryStatus": voting_ensemble_inventory_status(actual_runtime_bindings),
    }


def _evaluate_directional(
    module: SnapshotDirectionalStrategy | StrategyEvaluator,
    snapshot: VotingEnsembleEvaluationSnapshot,
    request: VotingEnsembleEvaluateRequest,
    regime_state: RegimeState,
    *,
    active: bool = True,
) -> VotingStrategyVote:
    if hasattr(module, "evaluate"):
        evaluate_params = inspect.signature(module.evaluate).parameters
        if "regime_state" in evaluate_params:
            signal = module.evaluate(snapshot, correlation_id=snapshot.snapshotHash, regime_state=regime_state)
        else:
            signal = module.evaluate(snapshot, correlation_id=snapshot.snapshotHash)
        return _vote_from_directional_signal(signal, regime_state, active=active)
    return _with_regime_features(_apply_strategy_fit(module(request), request), regime_state)


def _evaluate_request_with_strategy(request: VotingEnsembleEvaluateRequest, strategy: SnapshotDirectionalStrategy) -> VotingStrategyVote:
    snapshot = build_live_paper_snapshot(request.model_dump(mode="json"))
    if not snapshot.dataReadiness.ready:
        return _vote(
            strategy.strategyId,
            "trend",
            "Hold",
            0,
            "Snapshot data-readiness failed closed before strategy evaluation.",
            "voting_ensemble.strategy.fail_closed_data_readiness",
            data_ready=False,
        )
    regime_state = REGIME_CLASSIFIER.evaluate_snapshot(snapshot)
    return _vote_from_directional_signal(strategy.evaluate(snapshot, correlation_id=snapshot.snapshotHash), regime_state)


def _vote_from_directional_signal(signal: DirectionalStrategySignal, regime_state: RegimeState | None = None, *, active: bool = True) -> VotingStrategyVote:
    regime_features = _regime_vote_features(regime_state, signal.family) if regime_state else {}
    return VotingStrategyVote(
        strategy=signal.strategyName,
        family=signal.family,
        role="directional",
        signal=signal.signal,
        direction=_direction(signal.signal),
        confidence=signal.confidence,
        active=active,
        eligible=signal.eligible,
        dataReady=signal.dataReady,
        regimeFit=1.0,
        reliability=0.5,
        reason=" ".join(signal.evidence),
        features={
            "strategyId": signal.strategyId,
            "strategyVersion": signal.strategyVersion,
            "correlationId": signal.correlationId,
            "eventCorrelationId": signal.eventCorrelationId,
            "setupId": signal.setupId,
            "evidenceRole": signal.evidenceRole,
            "referenceLevelId": signal.referenceLevelId or "",
            "triggerTimestamp": signal.triggerTimestamp,
            "confirmationTimestamp": signal.confirmationTimestamp,
            "reasonCode": signal.reasonCodes[0],
            "reasonCodes": ",".join(signal.reasonCodes),
            **regime_features,
            **signal.features,
        },
    )


def _vote(
    strategy: str,
    family: str,
    signal: AlgoSignal,
    score: int,
    detail: str,
    reason_code: str,
    features: dict[str, FeatureValue] | None = None,
    *,
    role: str = "directional",
    data_ready: bool = True,
) -> VotingStrategyVote:
    confidence = max(0.0, min(1.0, score / 100))
    return VotingStrategyVote(
        strategy=strategy,
        family=family,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        signal=signal,
        direction=_direction(signal),
        confidence=confidence,
        active=True,
        eligible=signal != "Hold" and data_ready,
        dataReady=data_ready,
        regimeFit=1.0,
        reliability=0.5,
        reason=detail,
        features={"reasonCode": reason_code, **(features or {})},
    )


def _counts(votes: tuple[VotingStrategyVote, ...]) -> dict[str, int]:
    return {
        "Buy": sum(1 for vote in votes if vote.signal == "Buy"),
        "Sell": sum(1 for vote in votes if vote.signal == "Sell"),
        "Hold": sum(1 for vote in votes if vote.signal == "Hold"),
    }


def _aggregate_with_family_engine(
    directional_votes: tuple[VotingStrategyVote, ...],
    context_votes: tuple[VotingStrategyVote, ...],
    snapshot: VotingEnsembleEvaluationSnapshot,
    regime_state: RegimeState,
    safety_decision: GlobalGateDecision | None = None,
    settings: Any | None = None,
    payload: dict[str, Any] | None = None,
) -> EnsembleDecision:
    decided_at = _utc_timestamp(snapshot.evaluationTimestamp)
    session_date = decided_at.date()
    engine = _family_engine_for_settings(settings)
    reliability_estimates = _reliability_estimates(
        payload=payload or {},
        votes=directional_votes,
        snapshot=snapshot,
        regime_state=regime_state,
        settings=settings,
        decided_at=decided_at,
    )
    return engine.aggregate(
        strategySignals=[_strategy_signal_from_vote(vote, decided_at, session_date, snapshot.settingsHash) for vote in directional_votes],
        contextSignals=[_context_signal_from_vote(vote, decided_at, session_date, snapshot.settingsHash) for vote in context_votes],
        regimeState=regime_state,
        safetyDecision=safety_decision,
        reliabilityEstimates=reliability_estimates,
        decidedAt=decided_at,
        sessionDate=session_date,
    )


def _family_engine_for_settings(settings: Any | None) -> FamilyAwareDeterministicEnsemble:
    if settings is None:
        return FAMILY_AWARE_ENGINE
    profile = getattr(settings, "resolvedTradingProfile", None)
    thresholds = getattr(settings, "aggregationThresholds", None)
    try:
        config = FamilyAwareEnsembleConfig(
            minimumFinalScore=float(profile.minimumFinalScore),
            minimumIndependentSupportingFamilies=int(profile.minimumIndependentFamilySupport),
            minimumEligibleDirectionalStrategies=int(thresholds.minEligibleDirectionalVotes),
            reliabilityMode=_reliability_mode(thresholds),
            familyWeights=_family_weights(getattr(settings, "minimumFamilySupport", None)),
        )
    except Exception:
        return FAMILY_AWARE_ENGINE
    return FamilyAwareDeterministicEnsemble(config)


def _reliability_mode(thresholds: Any | None) -> OperatingMode:
    raw = str(getattr(thresholds, "reliabilityWeightingMode", "") or "").strip().upper()
    try:
        return OperatingMode[raw]
    except KeyError:
        return OperatingMode.SHADOW


def _reliability_sample_window(thresholds: Any | None) -> str:
    raw = str(getattr(thresholds, "reliabilitySampleWindow", "") or "").strip()
    return raw if raw in {"rolling_20_trades", "rolling_60_trades", "rolling_120_trades"} else "rolling_60_trades"


def _family_weights(minimum_family_support: Any | None) -> dict[StrategyFamily, float]:
    """Map settings family weights (snake_case keys) onto the engine's StrategyFamily keys.

    Settings permit a weight of 0 to mute a family; the engine config requires strictly
    positive weights, so 0 is floored to an epsilon that `_weighted_family_mean` treats
    as effectively muted.
    """
    raw = getattr(minimum_family_support, "familyWeights", None)
    weights: dict[StrategyFamily, float] = {family: 1.0 for family in FAMILY_ORDER}
    if not isinstance(raw, dict):
        return weights
    for key, value in raw.items():
        try:
            family = StrategyFamily[str(key).strip().upper()]
        except KeyError:
            continue
        if family not in weights:
            continue
        weight = float(value)
        weights[family] = weight if weight > 0.0 else 1e-6
    return weights


def _reliability_observations(payload: dict[str, Any]) -> list[VotingEnsembleReliabilityObservation]:
    """Point-in-time per-strategy outcome history supplied with the evaluate payload.

    History is an optional input: a malformed row is skipped rather than failing the
    evaluation, and an empty list leaves every strategy on the neutral fallback.
    """
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    raw = (
        payload.get("strategy_reliability_observations")
        or payload.get("strategyReliabilityObservations")
        or context.get("strategyReliabilityObservations")
        or []
    )
    if not isinstance(raw, list):
        return []
    observations: list[VotingEnsembleReliabilityObservation] = []
    for row in raw:
        if isinstance(row, VotingEnsembleReliabilityObservation):
            observations.append(row)
            continue
        if not isinstance(row, dict):
            continue
        try:
            observations.append(VotingEnsembleReliabilityObservation.model_validate(row))
        except Exception:
            continue
    return observations


def _reliability_estimates(
    *,
    payload: dict[str, Any],
    votes: tuple[VotingStrategyVote, ...],
    snapshot: VotingEnsembleEvaluationSnapshot,
    regime_state: RegimeState,
    settings: Any | None,
    decided_at: datetime,
) -> dict[str, StrategyReliabilityEstimate]:
    """Estimate each strategy's accuracy for the direction it is currently signalling."""
    observations = _reliability_observations(payload)
    if not observations:
        return {}
    thresholds = getattr(settings, "aggregationThresholds", None)
    mode = _reliability_mode(thresholds)
    sample_window = _reliability_sample_window(thresholds)
    estimator = VotingEnsembleReliabilityEstimator(
        VotingEnsembleReliabilityConfig(sampleWindow=sample_window, mode=mode)
    )
    scope = reliability_scope(snapshot, regime_state)
    regime = scope["regime"]
    session_segment = scope["sessionSegment"]
    volatility_state = scope["volatilityState"]
    estimates: dict[str, StrategyReliabilityEstimate] = {}
    for vote in votes:
        direction = _domain_signal(vote.signal)
        if direction == Signal.HOLD:
            continue
        strategy_id = _vote_strategy_id(vote)
        estimates[strategy_id] = estimator.estimate_one(
            observations=observations,
            strategy_id=strategy_id,
            direction=direction,
            regime=regime,
            session_segment=session_segment,
            volatility_state=volatility_state,
            sample_window=sample_window,
            evaluation_timestamp=decided_at,
            mode=mode,
        )
    return estimates


def reliability_scope(snapshot: VotingEnsembleEvaluationSnapshot, regime_state: RegimeState) -> dict[str, str]:
    """The bucket a reliability observation is filed under, and looked up by.

    Recording and lookup must agree exactly or every estimate silently falls back to
    neutral, so both sides call this one function.
    """
    return {
        "regime": str(regime_state.label),
        "sessionSegment": _session_segment(snapshot),
        "volatilityState": str(regime_state.volatility).lower(),
    }


def _session_policy_payload(payload: dict[str, Any], settings: Any) -> dict[str, Any] | None:
    """Where the session policy is configured.

    A caller-supplied policy wins so replay and backtest can pin the exact policy a
    recorded run used, rather than picking up whatever the live settings happen to say
    at the moment the replay is executed.
    """
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    for candidate in (
        payload.get("session_policy"),
        payload.get("sessionPolicy"),
        context.get("sessionPolicy"),
        getattr(settings, "sessionPolicy", None),
    ):
        if isinstance(candidate, dict):
            return candidate
    return None


def _session_segment(snapshot: VotingEnsembleEvaluationSnapshot) -> str:
    session_state = snapshot.sessionState if isinstance(snapshot.sessionState, dict) else {}
    for key in ("sessionSegment", "segment", "session_segment", "phase"):
        value = session_state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "regular_session"


def _local_gate_input(
    *,
    snapshot: VotingEnsembleEvaluationSnapshot,
    settings_hash: str,
    settings: Any | None,
    order_intent: str = "new_entry",
    upstream_global_gate: GlobalGateDecision | None,
    regime_state: RegimeState,
    ensemble_decision: EnsembleDecision | None,
    candidate: TradeCandidate | None,
    context_signals: tuple[ContextSignal, ...],
    execution_economics: dict[str, Any] | None,
) -> GlobalGateInput:
    evaluated_at = _utc_timestamp(snapshot.evaluationTimestamp)
    session_date = evaluated_at.date()
    account_state = _account_risk_state(snapshot)
    operational = _operational_state(snapshot, upstream_global_gate, regime_state)
    context_entry_blackout = any(
        signal.dataReady
        and bool(signal.features.get("entryBlackout", False))
        and str(signal.features.get("contextEffect") or "").lower() == "entry_block"
        for signal in context_signals
    )
    market = _market_state(snapshot)
    execution = _execution_state(snapshot)
    if execution_economics:
        market.update(_market_state_from_economics(execution_economics))
        execution.update(_execution_state_from_economics(execution_economics))
    risk = _risk_state(snapshot)
    return GlobalGateInput(
        orderIntent=order_intent,
        evaluatedAt=evaluated_at,
        sessionDate=session_date,
        symbol=snapshot.symbol,
        accountRiskState=account_state,
        candidate=candidate,
        candidateStrategyFamily=_candidate_family_from_decision(ensemble_decision),
        setupSubtype="voting_ensemble_one_minute",
        ensembleDecision=ensemble_decision,
        regimeState=regime_state,
        contextSignals=list(context_signals),
        orderPlan=None,
        featureSnapshot=snapshot.model_dump(mode="json"),
        dataState={
            "freshCandle": snapshot.dataReadiness.ready and "stale_spy_candle" not in snapshot.dataReadiness.staleInputs,
            "freshQuote": snapshot.nbbo is not None and "stale_spy_quote" not in snapshot.dataReadiness.staleInputs,
            "validBidAsk": bool(snapshot.nbbo and snapshot.nbbo.bid > 0 and snapshot.nbbo.ask >= snapshot.nbbo.bid),
            "monotonicTimestamps": "future_spy_candle" not in snapshot.dataReadiness.staleInputs,
            "requiredTimeframeSynchronized": snapshot.feedHealthStatus == "ready",
            "requiredAuxiliaryDataReady": not snapshot.dataReadiness.mandatoryFailures,
            "featureSchemaValid": bool(snapshot.snapshotVersion),
            "feedHealthy": snapshot.feedHealthStatus == "ready" and not bool(snapshot.operationalHealthSnapshot.get("feedDegraded", False)),
            "clockSynchronized": not bool(snapshot.operationalHealthSnapshot.get("clockDisagreement", False)),
            "decisionDeadlineValid": not bool((execution_economics or {}).get("latency", {}).get("decisionDeadlineExpired", False)),
        },
        operationalState={**operational, "settingsHash": settings_hash, "contextEntryBlackout": context_entry_blackout},
        brokerState={},
        marketState=market,
        executionState=execution,
        riskState=risk,
    )


def _upstream_global_gate_decision(snapshot: VotingEnsembleEvaluationSnapshot) -> GlobalGateDecision | None:
    source = snapshot.operationalHealthSnapshot.get("globalGateDecision") or snapshot.operationalHealthSnapshot.get("upstreamGlobalGateDecision")
    if not isinstance(source, dict):
        return None
    try:
        return GlobalGateDecision.model_validate(source)
    except Exception:
        return GlobalGateDecision(
            status=GateStatus.FAIL,
            eligible=False,
            dataReady=False,
            gateResults=[],
            reasonCodes=["voting_ensemble.local_gate.global_upstream_malformed"],
            explanation="Upstream global gate payload was malformed.",
            checkedAt=_utc_timestamp(snapshot.evaluationTimestamp),
            sessionDate=_utc_timestamp(snapshot.evaluationTimestamp).date(),
            configurationHash="malformed_upstream_global_gate",
        )


def _account_risk_state(snapshot: VotingEnsembleEvaluationSnapshot) -> AccountRiskState | None:
    payload = snapshot.accountRiskSnapshot
    equity = _number(payload, "equity")
    realized = _number(payload, "realizedPnlToday")
    if equity is None or realized is None:
        return None
    evaluated_at = _utc_timestamp(snapshot.evaluationTimestamp)
    intraday_high = _number(payload, "intradayEquityHigh")
    drawdown = _number(payload, "drawdownFromIntradayHighPercent")
    if drawdown is None and intraday_high and intraday_high > 0:
        drawdown = max(0.0, ((intraday_high - equity) / intraday_high) * 100.0)
    return AccountRiskState(
        accountId=str(payload.get("accountId") or "voting-ensemble-paper-account"),
        equity=equity,
        buyingPower=_number(payload, "buyingPower") or equity,
        openPositionNotional=_number(payload, "openPositionNotional") or 0.0,
        realizedPnlToday=realized,
        unrealizedPnlToday=_number(payload, "unrealizedPnlToday") or 0.0,
        estimatedExitCosts=_number(payload, "estimatedExitCosts") or 0.0,
        dailyNetPnlAfterExitCosts=_number(payload, "dailyNetPnlAfterExitCosts"),
        intradayEquityHigh=intraday_high,
        drawdownFromIntradayHighPercent=drawdown or 0.0,
        totalOpenRiskPercent=_number(payload, "totalOpenRiskPercent") or 0.0,
        totalSpyNotionalPercent=_number(payload, "totalSpyNotionalPercent") or 0.0,
        sameDirectionExposurePercent=_number(payload, "sameDirectionExposurePercent") or 0.0,
        tradesToday=int(_number(payload, "tradesToday") or 0),
        observedAt=_timestamp(payload.get("observedAt"), evaluated_at),
        sessionDate=evaluated_at.date(),
    )


def _operational_state(
    snapshot: VotingEnsembleEvaluationSnapshot,
    upstream_global_gate: GlobalGateDecision | None,
    regime_state: RegimeState,
) -> dict[str, Any]:
    state = snapshot.operationalHealthSnapshot
    session = snapshot.sessionState
    status = str(state.get("status") or "").lower()
    nominal = status in {"nominal", "ready", "ok", "healthy"}
    return {
        "tradingEnabled": bool(state.get("tradingEnabled", nominal)),
        "paperTradingMode": bool(state.get("paperTradingMode", True)) and not bool(state.get("liveTradingEnabled", False)),
        "marketOpen": bool(state.get("marketOpen", not bool(session.get("marketClosed", False)))),
        "entryWindowOpen": bool(state.get("entryWindowOpen", True)),
        "validSession": bool(state.get("validSession", str(session.get("phase") or "regular").lower() not in {"closed", "invalid"})),
        "feedDegraded": bool(state.get("feedDegraded", snapshot.feedHealthStatus != "ready")),
        "clockDisagreement": bool(state.get("clockDisagreement", False)),
        "executionFailureCooldownActive": bool(state.get("executionFailureCooldownActive", state.get("cooldownAfterExecutionFailure", False))),
        "eventRiskState": str(regime_state.features.get("eventRiskState") or ""),
        "upstreamGlobalGateDecision": upstream_global_gate.model_dump(mode="json") if upstream_global_gate else None,
    }


def _market_state(snapshot: VotingEnsembleEvaluationSnapshot) -> dict[str, Any]:
    state = snapshot.sessionState
    event = snapshot.economicEventState.state
    max_spread_dollars = _number(snapshot.operationalHealthSnapshot, "maximumSpreadDollars")
    return {
        "symbolHalt": bool(state.get("symbolHalt", state.get("marketHalt", False))),
        "luldPause": bool(state.get("luldPause", state.get("luld", False))),
        "marketWideCircuitBreaker": bool(state.get("marketWideCircuitBreaker", False)),
        "eventBlackout": _event_blackout_active(event),
        "spreadBps": snapshot.nbbo.spreadBasisPoints if snapshot.nbbo else None,
        "spreadDollars": snapshot.nbbo.spreadDollars if snapshot.nbbo else None,
        "maximumSpreadDollars": max_spread_dollars if max_spread_dollars is not None else 0.25,
        "realizedVolatilityPercentile": None,
    }


def _market_state_from_economics(economics: dict[str, Any]) -> dict[str, Any]:
    source_quote = economics.get("sourceQuote") if isinstance(economics.get("sourceQuote"), dict) else {}
    return {
        "spreadBps": _number(source_quote, "spreadBasisPoints"),
        "spreadDollars": _number(source_quote, "spreadDollars"),
        "maximumSpreadBps": _number(economics, "maximumSpreadBps"),
        "maximumSpreadDollars": _number(economics, "maximumSpreadDollars"),
    }


def _execution_state(snapshot: VotingEnsembleEvaluationSnapshot) -> dict[str, Any]:
    operational = snapshot.operationalHealthSnapshot
    spread = snapshot.nbbo.spreadDollars if snapshot.nbbo else None
    expected_slippage = _number(operational, "expectedSlippageDollars")
    if expected_slippage is None and spread is not None:
        expected_slippage = (spread / 2.0) + float(operational.get("slippagePerShare", 0.02) or 0.02)
    return {
        "liquidityShares": snapshot.nbbo.bidSize + snapshot.nbbo.askSize if snapshot.nbbo else None,
        "spreadBps": snapshot.nbbo.spreadBasisPoints if snapshot.nbbo else None,
        "expectedSlippageDollars": expected_slippage,
        "entryDistanceDollars": _number(operational, "entryDistanceDollars") or 0.0,
        "duplicateOrder": bool(operational.get("duplicateOrder", False)),
        "conflictingOrder": bool(operational.get("conflictingOrder", False)),
        "cooldownActive": bool(operational.get("cooldownActive", operational.get("executionFailureCooldownActive", False))),
    }


def _execution_state_from_economics(economics: dict[str, Any]) -> dict[str, Any]:
    return {
        "expectedSlippageDollars": _number(economics, "expectedSlippageDollars"),
        "maximumSlippageDollars": _number(economics, "maximumSlippageDollars"),
        "predictedGrossEdgeDollars": _number(economics, "predictedGrossEdgeDollars"),
        "predictedNetEdgeDollars": _number(economics, "predictedNetEdgeDollars"),
        "edgeToCostRatio": _number(economics, "edgeToCostRatio"),
        "minimumNetEdgeDollars": _number(economics, "minimumNetEdgeDollars"),
        "minimumEdgeToCostRatio": _number(economics, "minimumEdgeToCostRatio"),
        "availableFillableQuantity": _number(economics, "availableFillableQuantity"),
        "minimumFillableQuantity": _number(economics, "minimumFillableQuantity"),
        "participationRate": _number(economics, "participationRate"),
        "adverseSelectionRisk": _number(economics, "adverseSelectionRisk"),
        "executionEconomics": economics,
    }


def _risk_state(snapshot: VotingEnsembleEvaluationSnapshot) -> dict[str, Any]:
    account = snapshot.accountRiskSnapshot
    return {
        "consecutiveLosses": int(_number(account, "consecutiveLosses") or 0),
        "existingPositionConflict": bool(account.get("existingPositionConflict", False)),
    }


def _candidate_from_decision(
    snapshot: VotingEnsembleEvaluationSnapshot,
    decision: EnsembleDecision,
    settings: Any,
) -> TradeCandidate | None:
    signal = _domain_signal(decision.signal)
    if signal == Signal.HOLD or not decision.eligible or snapshot.nbbo is None:
        return None
    entry = snapshot.nbbo.ask if signal == Signal.BUY else snapshot.nbbo.bid
    profile = settings.resolvedTradingProfile
    stop_distance = max(
        float(settings.stopPolicy.fixedStopDistanceDollars) * float(profile.stopMultiplier),
        float(settings.stopPolicy.minimumStopDistanceDollars),
    )
    target_distance = stop_distance * float(settings.targetPolicy.takeProfitR) * float(profile.targetMultiplier)
    stop = entry - stop_distance if signal == Signal.BUY else entry + stop_distance
    target = entry + target_distance if signal == Signal.BUY else entry - target_distance
    evaluated_at = _utc_timestamp(snapshot.evaluationTimestamp)
    return TradeCandidate(
        candidateId=f"voting-ensemble-{snapshot.snapshotHash}",
        symbol=snapshot.symbol,
        signal=signal,
        direction=_domain_direction(signal),
        entryPrice=round(entry, 4),
        stopPrice=round(stop, 4),
        targetPrice=round(target, 4),
        quantity=0,
        confidence=decision.confidence,
        expectedValue=max(0.0, abs(decision.finalScore) - ((snapshot.nbbo.spreadDollars if snapshot.nbbo else 0.0) / max(entry, 0.01))),
        features={
            "supportingFamilies": len(decision.supportingFamilies),
            "finalScore": decision.finalScore,
            "strategyFamily": decision.supportingFamilies[0] if decision.supportingFamilies else "",
        },
        reasonCodes=[*list(decision.reasonCodes), "voting_ensemble.pipeline.quantity_pending_risk_budget"],
        explanation="Voting Ensemble deterministic candidate generated after family aggregation for local gate evaluation.",
        generatedAt=evaluated_at,
        sessionDate=evaluated_at.date(),
        configurationHash=f"{settings.configurationHash}:{decision.decisionId}",
    )


def _candidate_family_from_decision(decision: EnsembleDecision | None) -> StrategyFamily | None:
    if decision and decision.supportingFamilies:
        try:
            return StrategyFamily(decision.supportingFamilies[0])
        except ValueError:
            return None
    return None


def _blocked_gate_ids(decision: GlobalGateDecision) -> tuple[str, ...]:
    return tuple(result.gateId for result in decision.gateResults if result.status == GateStatus.FAIL.value and result.blocksTrading)


def _global_gate_blocks(decision: GlobalGateDecision | None) -> bool:
    return bool(decision and (not decision.eligible or decision.status == GateStatus.FAIL.value))


def _gate_reason_codes(decision: GlobalGateDecision | None) -> tuple[str, ...]:
    return tuple(decision.reasonCodes) if decision else ("voting_ensemble.local_gate.global_upstream_not_provided",)


def _elapsed_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000.0, 4)


def _decision_deadline_expired(snapshot: VotingEnsembleEvaluationSnapshot, settings: Any | None) -> bool:
    decision_age_seconds = _number(snapshot.operationalHealthSnapshot, "decisionAgeSeconds")
    if decision_age_seconds is None:
        return False
    deadline = getattr(getattr(settings, "latencyLimits", None), "commandDeadlineSeconds", 30)
    return float(decision_age_seconds) > float(deadline)


def _execution_economics_with_gate_duration(economics: Any | None, gate_duration_ms: float) -> dict[str, Any] | None:
    if economics is None:
        return None
    payload = economics.model_dump(mode="json")
    latency = payload.get("latency")
    if isinstance(latency, dict):
        latency["gateDurationMs"] = round(float(gate_duration_ms), 4)
    return payload


def _payload_identifier(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _capture_blocked_gates(
    payload: dict[str, Any],
    snapshot: VotingEnsembleEvaluationSnapshot,
    settings_hash: str,
    upstream_global_gate: GlobalGateDecision | None,
    local_gate: GlobalGateDecision,
) -> None:
    correlation_id = _payload_identifier(payload, "correlationId", "correlation_id") or snapshot.snapshotHash
    job_id = _payload_identifier(payload, "jobId", "job_id")
    common = {
        "writer": CAPTURE_WRITER,
        "correlation_id": correlation_id,
        "job_id": job_id,
        "settings_hash": settings_hash,
        "snapshot_timestamp": snapshot.evaluationTimestamp,
    }
    capture_operational_event(
        event_type="global_gate_decision",
        payload=upstream_global_gate.model_dump(mode="json") if upstream_global_gate else {"status": "not_provided"},
        **common,
    )
    capture_operational_event(
        event_type="local_gate_decision",
        payload=local_gate.model_dump(mode="json"),
        **common,
    )


def _risk_budget_for_candidate(
    *,
    snapshot: VotingEnsembleEvaluationSnapshot,
    settings: Any,
    decision: EnsembleDecision,
    candidate: TradeCandidate | None,
    local_gate: GlobalGateDecision,
    execution_economics: dict[str, Any] | None,
) -> Any | None:
    if candidate is None:
        return None
    account = _account_risk_state(snapshot)
    equity = account.equity if account else 0.0
    stop_distance = abs(candidate.entryPrice - (candidate.stopPrice or candidate.entryPrice))
    return resolve_voting_ensemble_risk_budget(
        _risk_budget_config(
            snapshot=snapshot,
            settings=settings,
            decision=decision,
            candidate=candidate,
            account=account,
            local_gate=local_gate,
            execution_economics=execution_economics,
        ),
        equity=equity,
        entry_price=candidate.entryPrice,
        stop_distance=stop_distance,
    )


def _order_plan_for_candidate(
    *,
    candidate: TradeCandidate | None,
    settings: Any,
    gate_decision: GlobalGateDecision,
    account: AccountRiskState | None,
    risk_budget: dict[str, Any] | None,
    evaluated_at: datetime,
) -> OrderPlan | None:
    if candidate is None:
        return None
    policy = _effective_policy_for_order_planner(
        settings=settings,
        account=account,
        risk_budget=risk_budget,
        evaluated_at=evaluated_at,
    )
    profile = settings.resolvedTradingProfile
    return EXECUTION_ADAPTER.translate_candidate_to_order(
        candidate=candidate,
        policy=policy,
        gateDecision=gate_decision,
        decidedAt=evaluated_at,
        sessionDate=evaluated_at.date(),
        orderType=settings.orderTypeAndLimitPolicy.orderType,
        limitOffsetBps=float(profile.limitOrderOffsetBps),
        timeInForce=settings.orderTypeAndLimitPolicy.timeInForce,
        maximumHoldingMinutes=int(profile.maximumHoldingMinutes),
    )


def _effective_policy_for_order_planner(
    *,
    settings: Any,
    account: AccountRiskState | None,
    risk_budget: dict[str, Any] | None,
    evaluated_at: datetime,
) -> EffectiveTradePolicy:
    profile = settings.resolvedTradingProfile
    baseline = BaselineTradingSettings(
        configurationHash=f"{settings.configurationHash}:baseline",
        startingCapital=float(settings.riskPerTrade.startingCapital),
        orderAllocationPercent=float(profile.orderAllocationPercent),
        dailyAllocationPercent=float(profile.dailyAllocationPercent),
        riskBudgetPercentOfOrder=float(settings.riskPerTrade.riskBudgetPercentOfOrder),
        maxTradesPerDay=int(profile.maxTradesPerDay),
        stopLossPercent=float(settings.stopPolicy.stopLossPercent),
        fixedStopDistanceDollars=float(settings.stopPolicy.fixedStopDistanceDollars),
        takeProfitR=float(settings.targetPolicy.takeProfitR),
        slippagePerShare=float(settings.slippageLimits.slippagePerShare),
        positionSizingMode=settings.positionSizingMode,
    )
    hard_limits = HardRiskLimits(
        maximumRiskPerTradePercent=float(profile.riskPerTradePercent),
        maximumDailyLossPercent=float(settings.dailyLossCap.maxDailyLossPercent),
        maximumPositionPercent=float(profile.maximumPositionPercent),
        maximumOrderNotionalPercent=float(profile.orderAllocationPercent),
        maximumDailyNotionalPercent=float(profile.dailyAllocationPercent),
        maximumShares=int(profile.maxShareQuantity),
        maximumTradesPerDay=int(profile.maxTradesPerDay),
        maximumSpreadBps=float(profile.maximumSpreadBps),
        configurationHash=f"{settings.configurationHash}:hard_limits",
        maxDailyLossPercent=float(settings.dailyLossCap.maxDailyLossPercent),
        maxOrderNotional=float((risk_budget or {}).get("order_limit") or (float(settings.riskPerTrade.startingCapital) * float(profile.orderAllocationPercent) / 100.0)),
        maxPositionNotional=float(settings.riskPerTrade.startingCapital) * float(profile.maximumPositionPercent) / 100.0,
        maxShareQuantity=int(profile.maxShareQuantity),
        minStopDistanceDollars=float(settings.stopPolicy.minimumStopDistanceDollars),
        maxSlippagePerShare=float(profile.maximumSlippagePerShare),
    )
    dynamic_bounds = DynamicPolicyBounds(
        minimumRiskMultiplier=float(settings.profileOverlayLimits.minimumRiskMultiplier),
        maximumRiskMultiplier=1.0,
        minimumTargetR=1.0,
        maximumTargetR=max(1.0, float(settings.targetPolicy.takeProfitR) * float(profile.targetMultiplier)),
        minimumHoldingMinutes=1,
        maximumHoldingMinutes=int(profile.maximumHoldingMinutes),
        minimumAtrStopMultiplier=0.5,
        maximumAtrStopMultiplier=4.0,
        minConfidence=0.0,
        minReliability=0.0,
        minRegimeFit=0.0,
        maxSpreadPercent=float(profile.maximumSpreadBps) / 100.0,
        maxParticipationPercent=1.0,
        minLiquidityShares=1,
        configurationHash=f"{settings.configurationHash}:dynamic_bounds",
    )
    account_state = account or AccountRiskState(
        accountId="voting-ensemble-paper-account",
        equity=0.0,
        buyingPower=0.0,
        openPositionNotional=0.0,
        realizedPnlToday=0.0,
        tradesToday=0,
        observedAt=evaluated_at,
        sessionDate=evaluated_at.date(),
    )
    return EffectiveTradePolicy(
        mode=OperatingMode.OFF,
        baselineSettings=baseline,
        hardRiskLimits=hard_limits,
        dynamicBounds=dynamic_bounds,
        accountRiskState=account_state,
        maxQuantity=int(min(int(profile.maxShareQuantity), int((risk_budget or {}).get("quantity") or 0))),
        maxNotional=hard_limits.maxOrderNotional,
        riskDollars=float((risk_budget or {}).get("risk_dollars") or 0.0),
        explanation="Voting Ensemble one-minute pipeline policy resolved from settings, gates, and risk budget before order planning.",
        effectiveAt=evaluated_at,
        sessionDate=evaluated_at.date(),
        configurationHash=f"{settings.configurationHash}:order_policy:{(risk_budget or {}).get('configuration_hash') or 'no_risk_budget'}",
    )


def _risk_budget_config(
    *,
    snapshot: VotingEnsembleEvaluationSnapshot,
    settings: Any,
    decision: EnsembleDecision,
    candidate: TradeCandidate,
    account: AccountRiskState | None,
    local_gate: GlobalGateDecision,
    execution_economics: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = settings.resolvedTradingProfile
    operational = snapshot.operationalHealthSnapshot
    features = snapshot.features.model_dump(mode="json")
    equity = account.equity if account else 0.0
    buying_power = account.buyingPower if account else 0.0
    current_notional_pct = account.totalSpyNotionalPercent if account else 100.0
    same_direction_pct = account.sameDirectionExposurePercent if account else 100.0
    maximum_position_pct = float(profile.maximumPositionPercent)
    local_remaining_pct = max(0.0, maximum_position_pct - max(current_notional_pct, same_direction_pct))
    global_allowance = _number(operational, "globalExposureAllowanceDollars")
    local_allowance = _number(operational, "localExposureAllowanceDollars")
    available_fillable = _number(execution_economics or {}, "availableFillableQuantity")
    net_edge = _number(execution_economics or {}, "predictedNetEdgeDollars")
    minimum_net_edge = _number(execution_economics or {}, "minimumNetEdgeDollars")
    edge_ratio = _number(execution_economics or {}, "edgeToCostRatio")
    minimum_edge_ratio = _number(execution_economics or {}, "minimumEdgeToCostRatio")
    net_edge_passed = (
        execution_economics is not None
        and net_edge is not None
        and minimum_net_edge is not None
        and net_edge > minimum_net_edge
        and (minimum_edge_ratio is None or (edge_ratio is not None and edge_ratio >= minimum_edge_ratio))
    )
    return {
        "candidateSignal": _algo_signal_from_domain(candidate.signal).upper(),
        "gatesPassed": bool(local_gate.eligible),
        "netEdgePassed": net_edge_passed,
        "profileAllowsEntries": profile.entryPermission == "allow_new_entries",
        "entriesBlocked": bool(profile.entriesBlocked),
        "riskPerTradePercent": float(profile.riskPerTradePercent),
        "orderAllocationPercent": float(profile.orderAllocationPercent),
        "dailyAllocationPercent": float(profile.dailyAllocationPercent),
        "maximumPositionPercent": maximum_position_pct,
        "profileMaximumShares": int(profile.maxShareQuantity),
        "availableBuyingPower": buying_power,
        "buyingPower": buying_power,
        "availableFillableQuantity": available_fillable if available_fillable is not None else 0.0,
        "currentOneMinuteVolume": _number(operational, "currentOneMinuteVolume") or _number(features, "volumeCurrent") or 0.0,
        "maximumVolumeParticipationPercent": _number(operational, "maximumVolumeParticipationPercent") or 1.0,
        "globalExposureAllowanceDollars": global_allowance if global_allowance is not None else equity * (maximum_position_pct / 100.0),
        "localExposureAllowanceDollars": local_allowance if local_allowance is not None else equity * (local_remaining_pct / 100.0),
        "voteEdge": abs(float(decision.finalScore)),
        "independentFamilySupport": len(decision.supportingFamilies),
        "minimumIndependentFamilySupport": int(profile.minimumIndependentFamilySupport),
        "regimeFit": _candidate_regime_fit(decision),
        "dynamicRiskCap": float(profile.riskMultiplier),
        "eventRiskCap": _number(operational, "eventRiskCap") if _number(operational, "eventRiskCap") is not None else 1.0,
        "drawdownCap": _drawdown_cap(account),
        "liquidityCap": _number(operational, "liquidityCap") if _number(operational, "liquidityCap") is not None else 1.0,
        "minimumTradableSize": int(_number(operational, "minimumTradableSize") or 1),
    }


def _candidate_regime_fit(decision: EnsembleDecision) -> float:
    fits = [
        float(signal.regimeFit)
        for signal in decision.strategySignals
        if signal.signal == decision.signal and signal.eligible and signal.dataReady
    ]
    return max(0.0, min(1.0, sum(fits) / len(fits))) if fits else 1.0


def _drawdown_cap(account: AccountRiskState | None) -> float:
    if account is None:
        return 0.0
    drawdown = max(0.0, float(account.drawdownFromIntradayHighPercent))
    if drawdown >= 5.0:
        return 0.0
    if drawdown >= 3.0:
        return 0.25
    if drawdown >= 2.0:
        return 0.50
    return 1.0


def _trace_step(
    stage: str,
    status: str,
    reason_codes: tuple[str, ...] | list[str],
    *,
    blocked_gate_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "blockedGateIds": list(blocked_gate_ids),
        "reasonCodes": [str(code) for code in reason_codes if code],
    }


def _event_blackout_active(event: dict[str, Any]) -> bool:
    if bool(event.get("eventBlackoutActive", False)):
        return True
    importance = str(event.get("importance") or event.get("eventImportance") or "").lower()
    state = str(event.get("state") or event.get("eventState") or "").lower()
    return importance in {"high", "critical"} and state in {"active", "imminent", "shock"}


def _timestamp(value: Any, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return _utc_timestamp(value)
    if isinstance(value, str):
        try:
            return _utc_timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return default
    return default


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _with_regime_features(vote: VotingStrategyVote, regime_state: RegimeState) -> VotingStrategyVote:
    return vote.model_copy(update={"features": {**vote.features, **_regime_vote_features(regime_state, vote.family)}})


def _regime_vote_features(regime_state: RegimeState | None, family: str) -> dict[str, FeatureValue]:
    if regime_state is None:
        return {}
    fit_key = {
        "trend": "trendFit",
        "breakout": "breakoutFit",
        "reversal": "reversalFit",
        "mean_reversion": "meanReversionFit",
        "event": "gapSessionFit",
    }.get(str(family), "")
    fit = regime_state.features.get(fit_key) if fit_key else None
    return {
        "regimeId": regime_state.regimeId,
        "regimeLabel": str(regime_state.label),
        "regimeTransitionState": str(regime_state.features.get("transitionState") or ""),
        "regimeConfigurationHash": str(regime_state.configurationHash),
        "classifierFamilyFit": round(float(fit), 4) if isinstance(fit, int | float) else 1.0,
        "strategyConfidenceRegimeFitSeparated": True,
    }


def _strategy_signal_from_vote(vote: VotingStrategyVote, evaluated_at: datetime, session_date: date, configuration_hash: str) -> StrategySignal:
    signal = _domain_signal(vote.signal)
    reason_codes = _vote_reason_codes(vote)
    features = _directional_contract_features(vote, evaluated_at)
    return StrategySignal(
        strategyId=_vote_strategy_id(vote),
        strategyName=vote.strategy,
        strategyVersion=str(features.get("strategyVersion") or "voting_ensemble_strategy_adapter_v1"),
        family=_domain_family(vote.family),
        role=StrategyRole.DIRECTIONAL,
        signal=signal,
        direction=_domain_direction(signal),
        confidence=vote.confidence,
        active=vote.active,
        eligible=vote.eligible,
        dataReady=vote.dataReady,
        setupDetected=vote.signal != "Hold",
        regimeFit=vote.regimeFit,
        reliability=vote.reliability,
        reasonCodes=reason_codes,
        explanation=vote.reason,
        features=features,
        requiredInputs=[],
        inputTimestamps={},
        evaluatedAt=evaluated_at,
        sessionDate=session_date,
        configurationHash=configuration_hash,
    )


def _context_signal_from_vote(vote: VotingStrategyVote, evaluated_at: datetime, session_date: date, configuration_hash: str) -> ContextSignal:
    features = dict(vote.features)
    features.setdefault("contextEffect", _context_effect_for_signal(vote.signal))
    features.setdefault("maxConfidenceAdjustment", 0.08)
    return ContextSignal(
        contextId=_vote_strategy_id(vote),
        signal=Signal.HOLD,
        direction=Direction.FLAT,
        confidence=vote.confidence,
        dataReady=vote.dataReady,
        explanation=vote.reason,
        features=features,
        evaluatedAt=evaluated_at,
        sessionDate=session_date,
        configurationHash=configuration_hash,
    )


def _family_scores_from_decision(decision: EnsembleDecision) -> dict[str, float]:
    scores: dict[str, float] = {}
    for family_score in decision.familyScores:
        family = _family_key(family_score.family)
        scores[family] = round(float(family_score.buyScore) - float(family_score.sellScore), 6)
    return scores


def _vote_reason_codes(vote: VotingStrategyVote) -> list[str]:
    raw_codes = vote.features.get("reasonCodes")
    if isinstance(raw_codes, str) and raw_codes:
        return [code for code in raw_codes.split(",") if code]
    reason_code = vote.features.get("reasonCode")
    return [str(reason_code)] if reason_code else []


def _domain_signal(signal: AlgoSignal | Signal | str) -> Signal:
    if isinstance(signal, Signal):
        return signal
    normalized = str(signal)
    if normalized in {"Buy", Signal.BUY.value}:
        return Signal.BUY
    if normalized in {"Sell", Signal.SELL.value}:
        return Signal.SELL
    return Signal.HOLD


def _algo_signal_from_domain(signal: Signal | str) -> AlgoSignal:
    normalized = signal.value if isinstance(signal, Signal) else str(signal)
    if normalized == Signal.BUY.value:
        return "Buy"
    if normalized == Signal.SELL.value:
        return "Sell"
    return "Hold"


def _domain_direction(signal: Signal) -> Direction:
    if signal == Signal.BUY:
        return Direction.LONG
    if signal == Signal.SELL:
        return Direction.SHORT
    return Direction.FLAT


def _domain_family(family: str) -> StrategyFamily:
    mapping = {
        "trend": StrategyFamily.TREND,
        "breakout": StrategyFamily.BREAKOUT,
        "reversal": StrategyFamily.REVERSAL,
        "mean_reversion": StrategyFamily.MEAN_REVERSION,
        "gap_session": StrategyFamily.GAP_SESSION,
        "event": StrategyFamily.MARKET_CONTEXT,
    }
    return mapping.get(str(family), StrategyFamily.MARKET_CONTEXT)


def _family_key(family: StrategyFamily | str) -> str:
    value = family.value if isinstance(family, StrategyFamily) else str(family)
    return value.lower()


def _context_effect_for_signal(signal: AlgoSignal) -> str:
    if signal == "Buy":
        return "confirm_or_strengthen_long_candidates"
    if signal == "Sell":
        return "confirm_or_strengthen_short_candidates"
    return "neutral"


def _utc_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _context_confirmation(final_signal: AlgoSignal, context_signals: tuple[VotingStrategyVote, ...]) -> VotingContextConfirmation:
    if final_signal == "Hold":
        return VotingContextConfirmation(
            outcome="not_applicable",
            detail="No active directional decision requires context confirmation.",
            evidence=tuple(vote.reason for vote in context_signals),
            confirmations=0,
            conflicts=0,
        )
    candidate_terms = ("long", "buy", "bull") if final_signal == "Buy" else ("short", "sell", "bear")
    opposing_terms = ("short", "sell", "bear") if final_signal == "Buy" else ("long", "buy", "bull")
    effects = [str(vote.features.get("contextEffect") or "neutral").lower() for vote in context_signals if vote.active and vote.dataReady]
    confirmations = sum(1 for effect in effects if "confirm" in effect and (any(term in effect for term in candidate_terms) or not any(term in effect for term in opposing_terms)))
    conflicts = sum(1 for effect in effects if "entry_block" in effect or "risk_reduction" in effect or "conflict" in effect or any(term in effect for term in opposing_terms))
    outcome = "confirms" if confirmations and not conflicts else "weakens" if conflicts and not confirmations else "mixed" if conflicts else "not_applicable"
    return VotingContextConfirmation(
        outcome=outcome,
        detail=f"Context confirmations={confirmations}, conflicts={conflicts}.",
        evidence=tuple(vote.reason for vote in context_signals),
        confirmations=confirmations,
        conflicts=conflicts,
    )


def _apply_strategy_fit(vote: VotingStrategyVote, request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    return vote


def _vote_strategy_id(vote: VotingStrategyVote) -> str:
    candidate = vote.features.get("strategyId")
    if isinstance(candidate, str):
        try:
            return canonical_strategy_id(candidate)
        except KeyError:
            return _slug(candidate)
    try:
        return canonical_strategy_id(vote.strategy)
    except KeyError:
        return _slug(vote.strategy)


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip("_") or "unknown_strategy"


def _directional_contract_features(vote: VotingStrategyVote, evaluated_at: datetime) -> dict[str, FeatureValue]:
    features = dict(vote.features)
    strategy_id = _vote_strategy_id(vote)
    evaluated = evaluated_at.isoformat()
    event_id = _first_feature_string(features, ("eventCorrelationId", "trendEventCorrelationId", "correlationId")) or f"{strategy_id}:{vote.direction}:{evaluated}"
    setup_id = _first_feature_string(features, ("setupId", "eventId")) or f"{strategy_id}:{event_id}"
    evidence_role = _first_feature_string(features, ("evidenceRole", "trendEvidenceRole", "strategyEvidenceRole")) or _default_evidence_role(strategy_id, vote.family)
    reference_level = _first_feature_string(features, ("referenceLevelId", "levelId", "openingRangeBoundaryId", "gapEventId")) or ""
    trigger = _first_feature_string(features, ("triggerTimestamp", "triggerTime", "setupTimestamp")) or evaluated
    confirmation = _first_feature_string(features, ("confirmationTimestamp", "confirmationTime")) or trigger
    return {
        **features,
        "eventCorrelationId": event_id,
        "setupId": setup_id,
        "evidenceRole": evidence_role,
        "referenceLevelId": reference_level,
        "triggerTimestamp": trigger,
        "confirmationTimestamp": confirmation,
    }


def _first_feature_string(features: dict[str, FeatureValue], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = features.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _default_evidence_role(strategy_id: str, family: str) -> str:
    mapping = {
        "multi_timeframe_trend_alignment": "timeframe_agreement",
        "first_pullback_after_open": "opening_pullback",
        "vwap_trend_continuation": "vwap_continuation",
        "failed_breakout_reversal": "failed_breakout_level_rejection",
        "liquidity_sweep_reversal": "liquidity_sweep_level_rejection",
        "bollinger_band_reversion": "bollinger_band_overextension",
        "atr_overextension_reversion": "atr_overextension",
        "opening_range_breakout": "opening_range_break",
        "gap_continuation_fade": "opening_gap_session",
    }
    return mapping.get(strategy_id, f"{family}_evidence")


def _runtime_strategy_id(module: SnapshotDirectionalStrategy | StrategyEvaluator) -> str:
    if hasattr(module, "strategyId"):
        return canonical_strategy_id(module.strategyId)
    return _runtime_evaluator_id(module)


def _runtime_evaluator_id(evaluator: StrategyEvaluator) -> str:
    for module_id, candidate in STRATEGY_EVALUATORS_BY_ID.items():
        if candidate is evaluator:
            return module_id
    return canonical_strategy_id(evaluator.__name__.removeprefix("evaluate_"))


def _direction(signal: AlgoSignal) -> int:
    if signal == "Buy":
        return 1
    if signal == "Sell":
        return -1
    return 0


def _simple_return(candles: tuple[Any, ...]) -> float:
    if len(candles) < 2 or candles[0].open <= 0:
        return 0.0
    return candles[-1].close / candles[0].open - 1
